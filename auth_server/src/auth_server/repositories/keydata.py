"""Data access repository for Keydata SA model"""

from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal, overload

from auxillary.data_structures.dto import AbstractResult
from auxillary.data_structures.repository import AbstractWorkRepository
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from redis.typing import EncodableT, FieldT
from sqlalchemy import insert, select, update

from auth_server.models.database import KeyData
from auth_server.strings import SelectionLockOption


@dataclass(slots=True, init=False)
class KeyPublicDataResult(AbstractResult):
    """
    DTO for KeyData ORM object, excluding private PEM bytes
    """

    resource_name: ClassVar[str] = KeyData.__tablename__

    kid: str
    alg: str
    curve: str
    epoch: datetime
    rotated_out_at: datetime
    expired_at: datetime
    public_pem: bytes
    manual_rotation: bool
    rotated_by: int

    @staticmethod
    def stringify_binary_fields(
        d: MutableMapping[Any, Any], *, encoding: str = "utf-8"
    ) -> None:
        for k, v in d.items():
            if isinstance(v, (bytes, bytearray)):
                d[k] = v.decode(encoding)

    def __json_repr__(self) -> dict[str, Any]:
        json_mapping: dict[str, Any] = super().__json_repr__()
        self.stringify_binary_fields(json_mapping)
        return json_mapping

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        cache_mapping: dict[FieldT, EncodableT] = super().__cache_repr__()
        self.stringify_binary_fields(cache_mapping)
        return cache_mapping


@dataclass(slots=True, init=False)
class KeyPrivateDataResult(KeyPublicDataResult):
    """
    DTO for KeyData ORM object, including private PEM bytes
    """

    private_pem: bytes


@dataclass(slots=True)
class KeydataRepository(AbstractWorkRepository):
    @overload
    async def get_keydata(
        self,
        key_id: str,
        *,
        public_only: Literal[True] = True,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> KeyPublicDataResult | None: ...

    @overload
    async def get_keydata(
        self,
        key_id: str,
        *,
        public_only: Literal[False] = False,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> KeyPrivateDataResult | None: ...

    async def get_keydata(
        self,
        key_id: str,
        *,
        public_only: bool = True,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> KeyPublicDataResult | KeyPrivateDataResult | None:
        statement = select(KeyData).where(KeyData.kid == key_id)
        if lock_args:
            statement = statement.with_for_update(
                **{lock_arg: True for lock_arg in lock_args}  # pyrefly: ignore
            )
        async with self._work_scoped_session() as session:
            keydata = (await session.execute(statement)).scalar_one_or_none()
            if not keydata:
                return None
            if public_only:
                return KeyPublicDataResult.construct_from_orm(keydata)
            return KeyPrivateDataResult.construct_from_orm(keydata)

    @overload
    async def get_relevant_keydata(
        self,
        limit: int | None = None,
        raise_on_empty: bool = False,
        *,
        public_data_only: Literal[True] = True,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> list[KeyPublicDataResult]: ...

    @overload
    async def get_relevant_keydata(
        self,
        limit: int | None = None,
        raise_on_empty: bool = False,
        *,
        public_data_only: Literal[False] = False,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> list[KeyPrivateDataResult]: ...

    async def get_relevant_keydata(
        self,
        limit: int | None = None,
        raise_on_empty: bool = False,
        *,
        public_data_only: bool = True,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> list[KeyPublicDataResult] | list[KeyPrivateDataResult]:
        statement = (
            select(KeyData)
            .where(KeyData.expired_at.is_(None))
            .order_by(KeyData.epoch.desc())
            .limit(limit)
        )

        if lock_args:
            statement = statement.with_for_update(
                **{lock_arg: True for lock_arg in lock_args}  # pyrefly: ignore
            )

        async with self._work_scoped_session() as session:
            keydata: list[KeyData] = list(
                (await session.execute(statement)).scalars().all()
            )
            if not keydata and raise_on_empty:
                raise ValueError("Key data empty")
            if public_data_only:
                return list(map(KeyPublicDataResult.construct_from_orm, keydata))
            return list(map(KeyPrivateDataResult.construct_from_orm, keydata))

    @overload
    async def insert_keydata(
        self,
        key_id: str,
        private_key: ec.EllipticCurvePrivateKey,
        public_key: ec.EllipticCurvePublicKey,
        alg: str,
        curve: ec.EllipticCurve,
        epoch: datetime | None = None,
        *,
        returning: Literal[False],
    ) -> None: ...

    @overload
    async def insert_keydata(
        self,
        key_id: str,
        private_key: ec.EllipticCurvePrivateKey,
        public_key: ec.EllipticCurvePublicKey,
        alg: str,
        curve: ec.EllipticCurve,
        epoch: datetime | None = None,
        *,
        returning: Literal[True],
    ) -> KeyPrivateDataResult: ...

    # TODO: Decouple key serialization logic from repository
    async def insert_keydata(
        self,
        key_id: str,
        private_key: ec.EllipticCurvePrivateKey,
        public_key: ec.EllipticCurvePublicKey,
        alg: str,
        curve: ec.EllipticCurve,
        epoch: datetime | None = None,
        *,
        returning: bool = False,
    ) -> KeyPrivateDataResult | None:
        async with self._work_scoped_session() as session:
            keydata: KeyData = (
                await session.execute(
                    insert(KeyData)
                    .values(
                        kid=key_id,
                        alg=alg,
                        curve=curve.name,
                        epoch=epoch or datetime.now(UTC),
                        private_pem=private_key.private_bytes(
                            encoding=Encoding.PEM,
                            format=PrivateFormat.PKCS8,
                            encryption_algorithm=NoEncryption(),
                        ),
                        public_pem=public_key.public_bytes(
                            encoding=Encoding.PEM,
                            format=PublicFormat.SubjectPublicKeyInfo,
                        ),
                    )
                    .returning(KeyData)
                )
            ).scalar_one()

            if returning:
                return KeyPrivateDataResult.construct_from_orm(keydata)

    @overload
    async def expire_keydata_with_threshold(
        self,
        threshold: datetime,
        expiry_time: datetime | None = None,
        *,
        return_expired: Literal[False] = False,
        public_data_only: bool = True,
    ) -> None: ...
    @overload
    async def expire_keydata_with_threshold(
        self,
        threshold: datetime,
        expiry_time: datetime | None = None,
        *,
        return_expired: Literal[True] = True,
        public_data_only: Literal[True] = True,
    ) -> list[KeyPublicDataResult]: ...
    @overload
    async def expire_keydata_with_threshold(
        self,
        threshold: datetime,
        expiry_time: datetime | None = None,
        *,
        return_expired: Literal[True] = True,
        public_data_only: Literal[False] = False,
    ) -> list[KeyPrivateDataResult]: ...

    async def expire_keydata_with_threshold(
        self,
        threshold: datetime,
        expiry_time: datetime | None = None,
        *,
        return_expired: bool = False,
        public_data_only: bool = True,
    ) -> list[KeyPublicDataResult] | list[KeyPrivateDataResult] | None:
        async with self._work_scoped_session() as session:
            expired_keys: list[KeyData] = list(
                (
                    await session.execute(
                        update(KeyData)
                        .where(KeyData.epoch < threshold)
                        .values(expired_at=expiry_time or datetime.now(UTC))
                        .returning(KeyData)
                    )
                )
                .scalars()
                .all()
            )

            if return_expired:
                if public_data_only:
                    return list(
                        map(KeyPublicDataResult.construct_from_orm, expired_keys)
                    )
                return list(map(KeyPrivateDataResult.construct_from_orm, expired_keys))

    @overload
    async def expire_keydata(
        self,
        kid: str,
        expiry_time: datetime | None = None,
        *,
        return_expired: Literal[False] = False,
        public_data_only: bool = True,
    ) -> None: ...
    @overload
    async def expire_keydata(
        self,
        kid: str,
        expiry_time: datetime | None = None,
        *,
        return_expired: Literal[True] = True,
        public_data_only: Literal[True] = True,
    ) -> KeyPublicDataResult | None: ...
    @overload
    async def expire_keydata(
        self,
        kid: str,
        expiry_time: datetime | None = None,
        *,
        return_expired: Literal[True] = True,
        public_data_only: Literal[False] = False,
    ) -> KeyPrivateDataResult | None: ...

    async def expire_keydata(
        self,
        kid: str,
        expiry_time: datetime | None = None,
        *,
        return_expired: bool = False,
        public_data_only: bool = True,
    ) -> KeyPublicDataResult | KeyPrivateDataResult | None:
        async with self._work_scoped_session() as session:
            expired_key: KeyData | None = (
                await session.execute(
                    update(KeyData)
                    .where(KeyData.kid == kid)
                    .values(expired_at=expiry_time or datetime.now(UTC))
                    .returning(KeyData)
                )
            ).scalar_one_or_none()

            if not expired_key:
                return None

            if return_expired:
                if public_data_only:
                    return KeyPublicDataResult.construct_from_orm(expired_key)
                return KeyPrivateDataResult.construct_from_orm(expired_key)

    @overload
    async def batch_expire_keydata(
        self,
        kids: Sequence[str],
        expiry_time: datetime | None = None,
        *,
        return_expired: Literal[False] = False,
        public_data_only: bool = True,
    ) -> None: ...
    @overload
    async def batch_expire_keydata(
        self,
        kids: Sequence[str],
        expiry_time: datetime | None = None,
        *,
        return_expired: Literal[True] = True,
        public_data_only: Literal[True] = True,
    ) -> list[KeyPublicDataResult]: ...
    @overload
    async def batch_expire_keydata(
        self,
        kids: Sequence[str],
        expiry_time: datetime | None = None,
        *,
        return_expired: Literal[True] = True,
        public_data_only: Literal[False] = False,
    ) -> list[KeyPrivateDataResult]: ...

    async def batch_expire_keydata(
        self,
        kids: Sequence[str],
        expiry_time: datetime | None = None,
        *,
        return_expired: bool = False,
        public_data_only: bool = True,
    ) -> list[KeyPublicDataResult] | list[KeyPrivateDataResult] | None:
        async with self._work_scoped_session() as session:
            expired_keys: list[KeyData] = list(
                (
                    await session.execute(
                        update(KeyData)
                        .where(KeyData.kid.in_(kids))
                        .values(expired_at=expiry_time or datetime.now(UTC))
                        .returning(KeyData)
                    )
                )
                .scalars()
                .all()
            )

            if not expired_keys:
                return [] if return_expired else None

            if return_expired:
                if public_data_only:
                    return list(
                        map(KeyPublicDataResult.construct_from_orm, expired_keys)
                    )
                return list(map(KeyPrivateDataResult.construct_from_orm, expired_keys))

    @overload
    async def get_expired_keys(
        self, *, public_data_only: Literal[True] = True
    ) -> list[KeyPublicDataResult]: ...
    @overload
    async def get_expired_keys(
        self, *, public_data_only: Literal[False] = False
    ) -> list[KeyPrivateDataResult]: ...

    async def get_expired_keys(
        self, *, public_data_only: bool = True
    ) -> list[KeyPublicDataResult] | list[KeyPrivateDataResult]:
        async with self._work_scoped_session() as session:
            expired_keys: list[KeyData] = list(
                (
                    await session.execute(
                        select(KeyData)
                        .where(KeyData.expired_at.isnot(None))
                        .order_by(KeyData.expired_at)
                    )
                )
                .scalars()
                .all()
            )
            if public_data_only:
                return list(map(KeyPublicDataResult.construct_from_orm, expired_keys))
            return list(map(KeyPrivateDataResult.construct_from_orm, expired_keys))

    @overload
    async def get_valid_inactive_keys(
        self,
        limit: int | None = None,
        *,
        public_data_only: Literal[True] = True,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> list[KeyPublicDataResult]: ...
    @overload
    async def get_valid_inactive_keys(
        self,
        limit: int | None = None,
        *,
        public_data_only: Literal[False] = False,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> list[KeyPrivateDataResult]: ...
    async def get_valid_inactive_keys(
        self,
        limit: int | None = None,
        *,
        public_data_only: bool = True,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> list[KeyPublicDataResult] | list[KeyPrivateDataResult]:
        statement = (
            select(KeyData)
            .where(
                (KeyData.expired_at.is_(None)) & (KeyData.rotated_out_at.isnot(None))
            )
            .limit(limit)
        )
        if lock_args:
            statement = statement.with_for_update(
                **{i: True for i in lock_args}  # pyrefly: ignore
            )
        async with self._work_scoped_session() as session:
            keys: list[KeyData] = list(
                (await session.execute(statement)).scalars().all()
            )
            if public_data_only:
                return list(map(KeyPublicDataResult.construct_from_orm, keys))
            return list(map(KeyPrivateDataResult.construct_from_orm, keys))

    @overload
    async def get_active_key(
        self,
        *,
        public_data_only: Literal[True] = True,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> KeyPublicDataResult | None: ...
    @overload
    async def get_active_key(
        self,
        *,
        public_data_only: Literal[False] = False,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> KeyPrivateDataResult | None: ...

    async def get_active_key(
        self,
        *,
        public_data_only: bool = True,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> KeyPublicDataResult | KeyPrivateDataResult | None:
        statement = select(KeyData).where(KeyData.rotated_out_at.is_(None))
        if lock_args:
            statement = statement.with_for_update(
                **{i: True for i in lock_args}  # pyrefly: ignore
            )

        async with self._work_scoped_session() as session:
            active_key: KeyData | None = (
                await session.execute(statement)
            ).scalar_one_or_none()
            if not active_key:
                return None
            if public_data_only:
                return KeyPublicDataResult.construct_from_orm(active_key)
            return KeyPrivateDataResult.construct_from_orm(active_key)

    # TODO: Make curve column an enum
    async def rotate_key(
        self,
        previous_key_id: str,
        new_key_id: str,
        new_key_public_pem: bytes | bytearray,
        new_key_private_pem: bytes | bytearray,
        *,
        alg: str = "ES256",
        curve: str = ec.SECP256K1.name,
        rotation_author: int | None = None,
        epoch: datetime | None = None,
        previous_key_rotation_time: datetime | None = None,
    ) -> None:
        epoch = epoch or datetime.now(UTC)
        previous_key_rotation_time = previous_key_rotation_time or epoch

        async with self._work_scoped_session() as session:
            await session.execute(
                update(KeyData)
                .where(KeyData.kid == previous_key_id)
                .values(
                    rotated_out_at=datetime.now(UTC),
                    manual_rotation=True,
                    rotated_by=rotation_author,
                )
            )

            # Add new key
            await session.execute(
                insert(KeyData).values(
                    kid=new_key_id,
                    curve=curve,
                    private_pem=new_key_private_pem,
                    public_pem=new_key_public_pem,
                    alg=alg,
                )
            )
