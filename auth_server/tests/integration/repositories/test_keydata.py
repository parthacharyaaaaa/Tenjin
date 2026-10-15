from datetime import UTC, datetime
from typing import Any, Final

import pytest
import pytest_asyncio
from auth_server.models.database import KeyData
from auth_server.repositories.keydata import (
    KeydataRepository,
    KeyPrivateDataResult,
    KeyPublicDataResult,
)
from auth_server.strings import SelectionLockOption
from auxillary.security.data_structures.jwks.enums import ECAlg
from auxillary.security.serialization import (
    pem_serialize_private_key,
    pem_serialize_public_key,
)
from cryptography.hazmat.primitives.asymmetric.ec import (
    SECP256K1,
    EllipticCurvePrivateKey,
    derive_private_key,
)
from cryptography.hazmat.primitives.serialization import PrivateFormat, PublicFormat
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.expression import insert

KEY_IDS: Final[tuple[int, ...]] = tuple(range(1, 6))
ROTATION_THRESHOLD: Final[int] = 4
EXPIRATION_THRESHOLD: Final[int] = 2


@pytest_asyncio.fixture(autouse=True, scope="module")
async def seed_keydata(pg_async_sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    values_dict: dict[str, Any] = {"curve": SECP256K1().name, "alg": ECAlg.ES256}
    async with pg_async_sessionmaker() as session:
        for i in KEY_IDS:
            dummy_key = derive_private_key(private_value=i, curve=SECP256K1())
            values_dict.update(
                {
                    "kid": str(i),
                    "private_pem": pem_serialize_private_key(
                        dummy_key, PrivateFormat.PKCS8
                    ),
                    "public_pem": pem_serialize_public_key(
                        dummy_key.public_key(), PublicFormat.SubjectPublicKeyInfo
                    ),
                    "epoch": datetime(year=2026, month=1, day=i, tzinfo=UTC),
                }
            )
            if i <= ROTATION_THRESHOLD:
                values_dict["rotated_out_at"] = datetime(
                    year=2026, month=2, day=i, tzinfo=UTC
                )
            if i <= EXPIRATION_THRESHOLD:
                values_dict["expired_at"] = datetime(
                    year=2026, month=3, day=i, tzinfo=UTC
                )
            await session.execute(insert(KeyData).values(values_dict))
            values_dict.pop("rotated_out_at", None)
            values_dict.pop("expired_at", None)
        await session.commit()


@pytest.mark.asyncio
async def test_get_relevant_keydata_order(
    keydata_repository: KeydataRepository,
) -> None:
    keydata = await keydata_repository.get_relevant_keydata()
    assert keydata == sorted(keydata, reverse=True, key=lambda x: x.epoch)


@pytest.mark.asyncio
async def test_locking(
    keydata_repository: KeydataRepository,
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    key_id: Final[str] = str(KEY_IDS[0])
    async with keydata_repository.unit_of_work():
        key = await keydata_repository.get_keydata(
            key_id=key_id, lock_args=(SelectionLockOption.READ,)
        )
        assert key is not None, "Locking prerequisite failed, key not found"
        # NOTE: A more canonical approach would've been using pytest.raises,
        # but in case we change db drivers, this approach would mean fewer
        # cascading changes
        async with pg_async_sessionmaker() as session:
            subsequent_key = (
                await session.execute(
                    select(KeyData)
                    .where(KeyData.kid == key_id)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()

            assert subsequent_key is None, (
                f"{SelectionLockOption.SKIP_LOCKED} failed to skip locked row"
            )


@pytest.mark.asyncio
async def test_get_active_key(keydata_repository: KeydataRepository) -> None:
    active_key = await keydata_repository.get_active_key()
    assert active_key is not None
    assert active_key.kid == str(KEY_IDS[-1])


@pytest.mark.asyncio
async def test_expired_keys_retrieval(keydata_repository: KeydataRepository) -> None:
    expired_keys = await keydata_repository.get_expired_keys()
    assert len(expired_keys) == 2
    assert KEY_IDS[:EXPIRATION_THRESHOLD] == tuple(
        map(lambda x: int(x.kid), expired_keys)
    )


@pytest.mark.asyncio
async def test_rotated_out_keys_retrieval(
    keydata_repository: KeydataRepository,
) -> None:
    valid_inactive_keys = await keydata_repository.get_valid_inactive_keys()
    assert len(valid_inactive_keys) == 2
    assert KEY_IDS[EXPIRATION_THRESHOLD:ROTATION_THRESHOLD] == tuple(
        map(lambda x: int(x.kid), valid_inactive_keys)
    )


@pytest.mark.asyncio
async def test_relevant_keys_retrieval(keydata_repository: KeydataRepository) -> None:
    relevant_keys = await keydata_repository.get_relevant_keydata()
    assert len(relevant_keys) == 3
    assert tuple(sorted(KEY_IDS[EXPIRATION_THRESHOLD:], reverse=True)) == tuple(
        map(lambda x: int(x.kid), relevant_keys)
    )


# State-changing DB tests that do not require COMMIT
# use an externally-scoped UoW with a throwaway session


@pytest.mark.asyncio
async def test_batch_expiry(
    keydata_repository: KeydataRepository,
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with pg_async_sessionmaker() as session:
        async with keydata_repository.external_unit_of_work(session):
            newly_expired_keys: list[
                KeyPublicDataResult
            ] = await keydata_repository.batch_expire_keydata(
                tuple(map(str, KEY_IDS[EXPIRATION_THRESHOLD:ROTATION_THRESHOLD])),
                return_expired=True,
            )
            assert (
                tuple(sorted((map(lambda x: int(x.kid), newly_expired_keys))))
                == KEY_IDS[EXPIRATION_THRESHOLD:ROTATION_THRESHOLD]
            )
            expired_keys = await keydata_repository.get_expired_keys()
            assert len(expired_keys) == 4
            assert KEY_IDS[:ROTATION_THRESHOLD] == tuple(
                map(lambda x: int(x.kid), expired_keys)
            )
    await session.close()


@pytest.mark.asyncio
async def test_keydata_expiry(
    keydata_repository: KeydataRepository,
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    key_id: Final[str] = str(KEY_IDS[-1])
    async with pg_async_sessionmaker() as session:
        async with keydata_repository.external_unit_of_work(session):
            expired_keydata: (
                KeyPublicDataResult | None
            ) = await keydata_repository.expire_keydata(key_id, return_expired=True)
            assert expired_keydata is not None
            assert expired_keydata.kid == key_id


@pytest.mark.asyncio
async def test_non_existent_keydata_expiry(
    keydata_repository: KeydataRepository,
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    key_id: Final[str] = "__NON_EXISTING_KEY_ID__"
    async with pg_async_sessionmaker(autocommit=False) as session:
        async with keydata_repository.external_unit_of_work(session):
            expired_keydata: (
                KeyPublicDataResult | None
            ) = await keydata_repository.expire_keydata(key_id, return_expired=True)
            assert expired_keydata is None


@pytest.mark.asyncio
async def test_keydata_threshold_expiry(
    keydata_repository: KeydataRepository,
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    expiry_threshold: datetime = datetime(
        2026, 1, KEY_IDS[:ROTATION_THRESHOLD][-1], second=1, tzinfo=UTC
    )
    async with pg_async_sessionmaker(autocommit=False) as session:
        async with keydata_repository.external_unit_of_work(session):
            expired_keydata: list[
                KeyPublicDataResult
            ] = await keydata_repository.expire_keydata_with_threshold(
                expiry_threshold, return_expired=True
            )
            assert (
                tuple(sorted(map(lambda x: int(x.kid), expired_keydata)))
                == KEY_IDS[EXPIRATION_THRESHOLD:ROTATION_THRESHOLD]
            )


@pytest.mark.asyncio
async def test_keydata_insertion(
    keydata_repository: KeydataRepository,
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    key_name: str = "test_key::foobar"
    dummy_key: Final[EllipticCurvePrivateKey] = derive_private_key(
        private_value=67, curve=SECP256K1()
    )
    async with pg_async_sessionmaker(autocommit=False) as session:
        async with keydata_repository.external_unit_of_work(session):
            inserted_keydata: KeyPrivateDataResult = (
                await keydata_repository.insert_keydata(
                    key_name,
                    pem_serialize_private_key(dummy_key, PrivateFormat.PKCS8),
                    pem_serialize_public_key(
                        dummy_key.public_key(), PublicFormat.SubjectPublicKeyInfo
                    ),
                    ECAlg.ES256,
                    curve=SECP256K1(),
                    returning=True,
                )
            )
            assert (
                await keydata_repository.get_keydata(key_name)
            ) == inserted_keydata.create_public_copy()


@pytest.mark.asyncio
async def test_key_rotation(
    keydata_repository: KeydataRepository,
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    dummy_key: Final[EllipticCurvePrivateKey] = derive_private_key(67, SECP256K1())
    new_key_id: Final[str] = "__NEW_KEY__"
    async with pg_async_sessionmaker(autocommit=False) as session:
        async with keydata_repository.external_unit_of_work(session):
            new_active_key: KeyPublicDataResult = await keydata_repository.rotate_key(
                str(KEY_IDS[-1]),
                new_key_id,
                pem_serialize_private_key(dummy_key, PrivateFormat.PKCS8),
                pem_serialize_public_key(
                    dummy_key.public_key(), PublicFormat.SubjectPublicKeyInfo
                ),
                returning=True,
            )

            assert new_active_key == await keydata_repository.get_active_key()

            assert (
                len(await keydata_repository.get_valid_inactive_keys())
                == len(KEY_IDS[EXPIRATION_THRESHOLD:ROTATION_THRESHOLD]) + 1
            )
