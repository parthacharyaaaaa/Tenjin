from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio
from auth_server.repositories.keydata import (
    KeydataRepository,
    KeyPrivateDataResult,
    KeyPublicDataResult,
)
from auth_server.subsystems.token_manager import TokenManager
from auxillary.security.serialization import (
    pem_serialize_private_key,
    pem_serialize_public_key,
)
from cryptography.hazmat.primitives.asymmetric.ec import (
    SECP256R1,
    EllipticCurvePrivateKey,
    derive_private_key,
)
from cryptography.hazmat.primitives.serialization import PrivateFormat, PublicFormat
from jwt import ImmatureSignatureError
from redis.asyncio import Redis
from sqlalchemy.util.typing import Final, LiteralString

TEST_ADMIN_NAME: Final[LiteralString] = "test_foobar_admin"

### Auxillary Classes ###


@dataclass(slots=True)
class DummyUserIdentification:
    sub: str
    sid: int


@dataclass(slots=True)
class DummyTokenClaims:
    jti: str


### END ###


### Local Fixtures ###
@pytest.fixture
def dummy_token_claims() -> DummyTokenClaims:
    return DummyTokenClaims("bar")


@pytest.fixture
def dummy_user_identification() -> DummyUserIdentification:
    return DummyUserIdentification("foo", 100)


@pytest_asyncio.fixture(scope="function", autouse=True)
async def token_manager_initialize_database_state(
    prepare_database_state: None,
) -> None:
    return


@pytest_asyncio.fixture(scope="function", autouse=True)
async def token_manager(
    keydata_repository: KeydataRepository,
    token_store_client: Redis,
    async_redis_client: Redis,
    dummy_key_config: SimpleNamespace,
    dummy_token_manager_config: SimpleNamespace,
    token_manager_initialize_database_state: None,
) -> TokenManager:
    token_manager: Final[TokenManager] = TokenManager(
        token_store_client,
        async_redis_client,
        keydata_repository,
        dummy_key_config,  # type: ignore
        dummy_token_manager_config,  # type: ignore
    )
    relevant_keys = await keydata_repository.get_relevant_keydata(
        public_data_only=False
    )
    token_manager.set_key_state(relevant_keys[0], {i.kid: i for i in relevant_keys[1:]})
    return token_manager


@pytest_asyncio.fixture(autouse=True)
async def cleanup_token_store(token_store_client: Redis) -> None:
    await token_store_client.flushall()


@pytest_asyncio.fixture
async def initialize_dummy_family(
    token_manager: TokenManager,
    dummy_user_identification: DummyUserIdentification,
    dummy_token_claims: DummyTokenClaims,
) -> None:
    with patch(
        "auth_server.subsystems.token_manager.uuid.uuid4",
    ) as uuid_mock:
        uuid_mock.return_value.hex = dummy_token_claims.jti
        await token_manager.issue_refresh_token(
            dummy_user_identification.sub,
            dummy_user_identification.sid,
        )


### END ###


### TESTS ###
@pytest.mark.asyncio
async def test_repeat_refresh_token_issuance(
    token_manager: TokenManager,
    token_store_client: Redis,
    dummy_user_identification: DummyUserIdentification,
) -> None:
    jti: Final[str] = "bar"
    iat = int(datetime.now(tz=UTC).timestamp() + 1000)
    expected_family_id: Final[str] = token_manager.generate_family_list_id(
        dummy_user_identification.sub
    )
    expected_exp: Final[int] = iat + int(
        token_manager.token_manager_config.REFRESH_LIFETIME.total_seconds()
    )
    expected_family_record: Final[str] = token_manager.generate_family_record(
        jti, expected_exp
    )
    with (
        patch("time.time", return_value=iat),
        patch(
            "auth_server.subsystems.token_manager.uuid.uuid4",
        ) as uuid_mock,
    ):
        uuid_mock.return_value.hex = jti
        await token_manager.issue_refresh_token(
            dummy_user_identification.sub,
            dummy_user_identification.sid,
        )
        pushed_family_data = await token_store_client.lrange(expected_family_id, 0, -1)  # pyrefly: ignore[not-async]
        assert pushed_family_data == [expected_family_record]


@pytest.mark.asyncio
async def test_family_invalidation(
    token_manager: TokenManager,
    dummy_user_identification: DummyUserIdentification,
    token_store_client: Redis,
    initialize_dummy_family: None,
) -> None:
    family_id: Final[str] = token_manager.generate_family_list_id(
        dummy_user_identification.sub
    )
    await token_manager.invalidate_family(family_id)
    assert await token_store_client.llen(family_id) == 0  # pyrefly: ignore[not-async]


@pytest.mark.asyncio
async def test_token_reissuance_rejects_premature_refreshes(
    dummy_user_identification: DummyUserIdentification,
    token_manager: TokenManager,
) -> None:
    refresh_token: Final[str] = await token_manager.issue_refresh_token(
        dummy_user_identification.sub, dummy_user_identification.sid
    )
    with pytest.raises(
        ImmatureSignatureError, match=r"The token is not yet valid \(nbf\)"
    ):
        await token_manager.reissue_token_pair(refresh_token)


@pytest.mark.asyncio
async def test_token_reissuance_updates_family_list_in_bound(
    dummy_user_identification: DummyUserIdentification,
    token_manager: TokenManager,
    token_store_client: Redis,
) -> None:
    family_id: Final[str] = token_manager.generate_family_list_id(
        dummy_user_identification.sub
    )
    last_refresh_token: str = ""
    with patch.object(
        token_manager.token_manager_config, "ACCESS_LIFETIME", timedelta()
    ):
        refresh_token: str = await token_manager.issue_refresh_token(
            dummy_user_identification.sub, dummy_user_identification.sid
        )
        assert await token_store_client.llen(family_id) == 1  # pyrefly: ignore[not-async]
        for i in range(token_manager.token_manager_config.MAX_TOKENS_PER_FAMILY - 1):
            # print(time.monotonic())
            # print(jwt.decode(
            #     jwt=refresh_token,
            #     key=token_manager._key_mapping[token_manager._active_key].public_pem,
            #     algorithms=["ES256"], leeway=token_manager.token_manager_config.LEEWAY))
            refresh_token, _access_token = await token_manager.reissue_token_pair(
                refresh_token
            )
            assert await token_store_client.llen(family_id) == i + 2  # pyrefly: ignore[not-async]
            if i == token_manager.token_manager_config.MAX_TOKENS_PER_FAMILY - 2:
                last_refresh_token = refresh_token
        await token_manager.reissue_token_pair(last_refresh_token)
        assert (
            await token_store_client.llen(family_id)
            == token_manager.token_manager_config.MAX_TOKENS_PER_FAMILY
        )  # pyrefly: ignore[not-async]


@pytest.mark.asyncio
async def test_local_keydata_updation(
    token_manager: TokenManager, keydata_repository: KeydataRepository
) -> None:
    active_key: KeyPublicDataResult = await keydata_repository.get_active_key()  # pyrefly: ignore[bad-assignment]
    assert active_key is not None  # Should always pass
    new_dummy_key: Final[EllipticCurvePrivateKey] = derive_private_key(
        private_value=67, curve=SECP256R1()
    )
    new_key: Final[KeyPrivateDataResult] = await keydata_repository.rotate_key(
        active_key.kid,
        "NEW_ROTATED_KEY",
        pem_serialize_public_key(
            new_dummy_key.public_key(), PublicFormat.SubjectPublicKeyInfo
        ),
        pem_serialize_private_key(new_dummy_key, PrivateFormat.PKCS8),
        returning=True,
        public_only=False,
    )
    await token_manager.update_local_store(await keydata_repository.get_jwks())
    assert token_manager._active_key == new_key.kid
    assert token_manager._active_key_private_pem == new_key.private_pem
