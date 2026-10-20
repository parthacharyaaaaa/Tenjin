import asyncio
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import orjson
import pytest
import pytest_asyncio
from auth_server.config import JWKSConfigModel
from auth_server.models.database import KeyData
from auth_server.repositories.keydata import (
    EllipticCurveJWKResult,
    KeydataRepository,
    KeyPublicDataResult,
)
from auth_server.subsystems.key_manager import (
    KeyLifecycleManager,
    SyncedStoreKeyStateManager,
)
from auth_server.subsystems.token_manager import TokenManager
from auxillary.security.data_structures.jwks.enums import ECAlg
from auxillary.security.serialization import (
    PrivateFormat,
    PublicFormat,
    pem_serialize_private_key,
    pem_serialize_public_key,
)
from cryptography.hazmat.primitives.asymmetric.ec import (
    SECP256R1,
    EllipticCurvePrivateKey,
    derive_private_key,
)
from jwt.exceptions import InvalidKeyError
from jwt.utils import base64url_encode
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio.session import AsyncSession
from sqlalchemy.sql.expression import delete
from sqlalchemy.util.typing import Final


### Local Fixtures ###
@pytest_asyncio.fixture
async def prep_db(
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
    keydata_repository: KeydataRepository,
    dummy_jwks_config: JWKSConfigModel,
) -> None:
    async with pg_async_sessionmaker() as session:
        await session.execute(delete(KeyData))
        await session.commit()

    async with keydata_repository.unit_of_work():
        dummy_key: EllipticCurvePrivateKey = derive_private_key(1, SECP256R1())
        await keydata_repository.insert_keydata(
            "1",
            pem_serialize_private_key(dummy_key, PrivateFormat.PKCS8),
            pem_serialize_public_key(
                dummy_key.public_key(), PublicFormat.SubjectPublicKeyInfo
            ),
            ECAlg.ES256,
            SECP256R1(),
            epoch=datetime(2026, 1, 1, tzinfo=UTC),
        )
        for i in range(2, dummy_jwks_config.JWKS_CAP * 2):
            dummy_key: EllipticCurvePrivateKey = derive_private_key(i, SECP256R1())
            await keydata_repository.rotate_key(
                str(i - 1),
                str(i),
                pem_serialize_public_key(
                    dummy_key.public_key(), PublicFormat.SubjectPublicKeyInfo
                ),
                pem_serialize_private_key(dummy_key, PrivateFormat.PKCS8),
                alg=ECAlg.ES256,
                curve=SECP256R1,
                epoch=datetime(2026, 1, i, tzinfo=UTC),
            )
            if i > dummy_jwks_config.JWKS_CAP:
                await keydata_repository.expire_keydata(
                    str(i - dummy_jwks_config.JWKS_CAP),
                    expiry_time=datetime(2026, 1, i, tzinfo=UTC),
                )


@pytest_asyncio.fixture
async def prep_synced_key_store(
    prep_db: None,
    keydata_repository: KeydataRepository,
    synced_key_store_manager: SyncedStoreKeyStateManager,
) -> None:
    await synced_key_store_manager.set_jwks(await keydata_repository.get_jwks())


@pytest_asyncio.fixture
async def prep_token_manager(
    prep_db: None, keydata_repository: KeydataRepository, token_manager: TokenManager
) -> None:
    relevant_keys = await keydata_repository.get_relevant_keydata(
        public_data_only=False
    )
    token_manager.set_key_state(relevant_keys[0], {i.kid: i for i in relevant_keys[1:]})


@pytest_asyncio.fixture(autouse=True)
async def pre_test_prepper(
    prep_db: None,
    prep_token_manager: None,
    prep_synced_key_store: None,
) -> None:
    return


### END ###


@pytest.mark.asyncio
async def test_normal_key_invalidation(
    key_lifecycle_manager: KeyLifecycleManager,
    keydata_repository: KeydataRepository,
    synced_key_store_manager: SyncedStoreKeyStateManager,
    token_manager: TokenManager,
) -> None:
    target_key: Final[KeyPublicDataResult] = (
        await keydata_repository.get_relevant_keydata()
    )[-1]
    await key_lifecycle_manager.invalidate_key(target_key.kid)

    with pytest.raises(
        InvalidKeyError,
        match="This key is not recognised, meaning it is possibly tampered, forged, or simply expired a long time ago.",
    ):
        dummy_token: str = (
            b".".join(
                (
                    base64url_encode(orjson.dumps({"kid": target_key.kid})),
                    base64url_encode(b"pld"),
                    base64url_encode(b"sig"),
                )
            )
        ).decode("utf-8")
        await token_manager.decode_token(dummy_token)

    new_db_relevant_keydata: Final[
        tuple[EllipticCurveJWKResult, ...]
    ] = await keydata_repository.get_jwks()
    assert len(new_db_relevant_keydata) == 2
    assert all(target_key.kid != jwk.kid for jwk in new_db_relevant_keydata)
    new_ss_jwks: Final[
        list[EllipticCurveJWKResult]
    ] = await synced_key_store_manager.get_jwks()
    assert new_db_relevant_keydata == tuple(new_ss_jwks), (
        "post-invalidation JWKS states mismatch"
    )


@pytest.mark.asyncio
async def test_key_invalidation_on_synced_store_mismatch(
    key_lifecycle_manager: KeyLifecycleManager,
    keydata_repository: KeydataRepository,
    synced_key_store_manager: SyncedStoreKeyStateManager,
    token_manager: TokenManager,
) -> None:
    target_key: Final[KeyPublicDataResult] = (
        await keydata_repository.get_relevant_keydata()
    )[-1]
    await synced_key_store_manager.set_jwks(
        [await keydata_repository.get_active_key()]  # type: ignore
    )
    message_mapping: dict[str, str] = {}
    await key_lifecycle_manager.invalidate_key(
        target_key.kid, intermediate_message_mapping=message_mapping
    )
    assert message_mapping == {
        "keylist_integrity_warning": "Synced keylist state was inconsistent and hence regenerated through database"
    }
    with pytest.raises(
        InvalidKeyError,
        match="This key is not recognised, meaning it is possibly tampered, forged, or simply expired a long time ago.",
    ):
        dummy_token: str = (
            b".".join(
                (
                    base64url_encode(orjson.dumps({"kid": target_key.kid})),
                    base64url_encode(b"pld"),
                    base64url_encode(b"sig"),
                )
            )
        ).decode("utf-8")
        await token_manager.decode_token(dummy_token)

    new_db_relevant_keydata: Final[
        tuple[EllipticCurveJWKResult, ...]
    ] = await keydata_repository.get_jwks()
    assert len(new_db_relevant_keydata) == 2
    assert all(target_key.kid != jwk.kid for jwk in new_db_relevant_keydata)
    new_ss_jwks: Final[
        list[EllipticCurveJWKResult]
    ] = await synced_key_store_manager.get_jwks()
    assert new_db_relevant_keydata == tuple(new_ss_jwks), (
        "post-invalidation JWKS states mismatch"
    )


@pytest.mark.asyncio
async def test_standard_keystore_clean(
    key_lifecycle_manager: KeyLifecycleManager,
    keydata_repository: KeydataRepository,
    synced_key_store_manager: SyncedStoreKeyStateManager,
) -> None:
    valid_inactive_kids = tuple(
        i.kid for i in await keydata_repository.get_valid_inactive_keys()
    )
    active_kid, invalidated_kids = await key_lifecycle_manager.clean_keystore()
    active_key: KeyPublicDataResult | None = await keydata_repository.get_active_key()
    assert active_key is not None
    assert active_key.kid == active_kid
    assert valid_inactive_kids == invalidated_kids
    db_jwks: Final[
        tuple[EllipticCurveJWKResult, ...]
    ] = await keydata_repository.get_jwks()
    assert len(db_jwks) == 1
    assert db_jwks[0].kid == (await keydata_repository.get_active_key()).kid  # type: ignore
    ss_jwks: Final[
        list[EllipticCurveJWKResult]
    ] = await synced_key_store_manager.get_jwks()
    assert db_jwks == tuple(ss_jwks), "post-invalidation JWKS states mismatch"


@pytest.mark.asyncio
async def test_standard_key_rotation(
    key_lifecycle_manager: KeyLifecycleManager,
    keydata_repository: KeydataRepository,
    synced_key_store_manager: SyncedStoreKeyStateManager,
) -> None:
    expected_inactive_keys: Final[
        list[EllipticCurveJWKResult]
    ] = await synced_key_store_manager.get_jwks()
    new_key = await key_lifecycle_manager.rotate_key()
    new_db_relevant_keydata = await keydata_repository.get_relevant_keydata()
    assert new_db_relevant_keydata[0].kid == new_key.kid
    assert expected_inactive_keys == [k.as_jwk() for k in new_db_relevant_keydata[1:]]
    new_ss_jwks = await synced_key_store_manager.get_jwks()
    assert tuple(i.as_jwk() for i in new_db_relevant_keydata) == tuple(new_ss_jwks), (
        "post-invalidation JWKS states mismatch"
    )


@pytest.mark.asyncio
async def test_token_manager_updation_poller_starts_and_stops(
    key_lifecycle_manager: KeyLifecycleManager,
    token_manager: TokenManager,
) -> None:
    with patch.object(
        token_manager.token_manager_config,
        "POLL_INTERVAL",
        timedelta(milliseconds=10),
    ):
        key_lifecycle_manager.start_token_manager_polling()
        polling_task = key_lifecycle_manager._token_manager_polling_task
        await asyncio.sleep(0)

        assert polling_task.done() is False
        assert (
            getattr(polling_task.get_coro(), "__name__", None)
            == "token_manager_updation_poller"
        )

        await key_lifecycle_manager.stop_token_manager_polling()

    assert polling_task.cancelled()
    assert key_lifecycle_manager._token_manager_polling_task is None


@pytest.mark.asyncio
async def test_token_manager_updation_poller_reconciles_local_key_state(
    key_lifecycle_manager: KeyLifecycleManager,
    keydata_repository: KeydataRepository,
    synced_key_store_manager: SyncedStoreKeyStateManager,
    token_manager: TokenManager,
) -> None:
    previous_active_key = await keydata_repository.get_active_key()
    assert previous_active_key is not None
    original_key_ids = set(token_manager._key_mapping)
    oldest_key_id = (await keydata_repository.get_valid_inactive_keys())[-1].kid
    new_private_key = derive_private_key(100, SECP256R1())

    new_active_key = await keydata_repository.rotate_key(
        previous_active_key.kid,
        "poller-new-active",
        pem_serialize_public_key(
            new_private_key.public_key(), PublicFormat.SubjectPublicKeyInfo
        ),
        pem_serialize_private_key(new_private_key, PrivateFormat.PKCS8),
        alg=ECAlg.ES256,
        curve=SECP256R1,
        epoch=datetime(2026, 2, 1, tzinfo=UTC),
        returning=True,
        public_only=False,
    )
    await keydata_repository.expire_keydata(oldest_key_id)
    await synced_key_store_manager.set_jwks(await keydata_repository.get_jwks())
    expected_key_ids = (original_key_ids - {oldest_key_id}) | {new_active_key.kid}
    state_updated = asyncio.Event()
    update_local_store = TokenManager.update_local_store

    async def observed_update_local_store(
        manager: TokenManager,
        global_jwks: Iterable[EllipticCurveJWKResult],
    ) -> None:
        await update_local_store(manager, global_jwks)
        if (
            set(manager._key_mapping) == expected_key_ids
            and manager._active_key == new_active_key.kid
        ):
            state_updated.set()

    with (
        patch.object(
            token_manager.token_manager_config,
            "POLL_INTERVAL",
            timedelta(milliseconds=10),
        ),
        patch.object(
            TokenManager,
            "update_local_store",
            new=observed_update_local_store,
        ),
    ):
        key_lifecycle_manager.start_token_manager_polling()
        polling_task = key_lifecycle_manager._token_manager_polling_task
        try:
            await asyncio.wait_for(state_updated.wait(), timeout=2)
        finally:
            await key_lifecycle_manager.stop_token_manager_polling()

    assert polling_task.cancelled()
    assert set(token_manager._key_mapping) == expected_key_ids
    assert oldest_key_id not in token_manager._key_mapping
    assert token_manager._active_key_private_pem == new_active_key.private_pem


@pytest.mark.asyncio
async def test_stopped_token_manager_updation_poller_no_longer_mutates_state(
    key_lifecycle_manager: KeyLifecycleManager,
    keydata_repository: KeydataRepository,
    synced_key_store_manager: SyncedStoreKeyStateManager,
    token_manager: TokenManager,
) -> None:
    initial_key_ids = set(token_manager._key_mapping)
    active_key = await keydata_repository.get_active_key()
    assert active_key is not None

    with patch.object(
        token_manager.token_manager_config,
        "POLL_INTERVAL",
        timedelta(milliseconds=10),
    ):
        key_lifecycle_manager.start_token_manager_polling()
        await asyncio.sleep(0.025)
        await key_lifecycle_manager.stop_token_manager_polling()

        await synced_key_store_manager.set_jwks((active_key,))
        await asyncio.sleep(0.025)

    assert set(token_manager._key_mapping) == initial_key_ids
    assert key_lifecycle_manager._token_manager_polling_task is None
