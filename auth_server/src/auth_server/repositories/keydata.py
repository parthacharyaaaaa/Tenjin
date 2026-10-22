"""Data access repository for Keydata SA model"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal, overload

from auxillary.data_structures.repository import AbstractWorkRepository
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.expression import ColumnExpressionArgument
from sqlalchemy.sql.operators import and_

from auth_server.datastructures.exceptions.keys import EmptyKeysError
from auth_server.models.data_transport.keydata import (
    ActiveKeyPrivateDataResult,
    ActiveKeyPublicDataResult,
    EllipticCurveJWKResult,
    ExpiredKeyDataResult,
    RotatedKeyDataResult,
)
from auth_server.models.database import KeyData
from auth_server.strings import SelectionLockOption


@dataclass(slots=True)
class KeydataRepository(AbstractWorkRepository):
    @overload
    async def get_keydata(
        self,
        key_id: str,
        *,
        include_expired: Literal[False] = False,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> ActiveKeyPublicDataResult | RotatedKeyDataResult | None: ...

    @overload
    async def get_keydata(
        self,
        key_id: str,
        *,
        include_expired: Literal[True] = True,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> (
        ActiveKeyPublicDataResult | RotatedKeyDataResult | ExpiredKeyDataResult | None
    ): ...

    async def get_keydata(
        self,
        key_id: str,
        *,
        include_expired: bool = False,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> ActiveKeyPublicDataResult | RotatedKeyDataResult | ExpiredKeyDataResult | None:
        selection_clauses: list[ColumnExpressionArgument] = [KeyData.kid == key_id]
        if not include_expired:
            selection_clauses.append(KeyData.expired_at.is_(None))
        statement = select(KeyData).where(and_(*selection_clauses))
        if lock_args:
            statement = statement.with_for_update(
                **{lock_arg: True for lock_arg in lock_args}  # pyrefly: ignore
            )
        async with self._work_scoped_session() as session:
            keydata = (await session.execute(statement)).scalar_one_or_none()
            if not keydata:
                return None
            if keydata.expired_at:
                return ExpiredKeyDataResult.construct_from_orm(keydata)
            if keydata.rotated_out_at:
                return RotatedKeyDataResult.construct_from_orm(keydata)
            return ActiveKeyPublicDataResult.construct_from_orm(keydata)

    @overload
    async def get_rotated_keydata(
        self,
        key_id: str,
        *,
        include_expired: Literal[False] = False,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> RotatedKeyDataResult | None: ...

    @overload
    async def get_rotated_keydata(
        self,
        key_id: str,
        *,
        include_expired: Literal[True] = True,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> RotatedKeyDataResult | ExpiredKeyDataResult | None: ...

    async def get_rotated_keydata(
        self,
        key_id: str,
        *,
        include_expired: bool = False,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> RotatedKeyDataResult | ExpiredKeyDataResult | None:
        statement = select(KeyData).where(
            (KeyData.kid == key_id) & (KeyData.rotated_out_at.isnot(None))
        )
        if lock_args:
            statement = statement.with_for_update(
                **{lock_arg: True for lock_arg in lock_args}  # pyrefly: ignore
            )
        async with self._work_scoped_session() as session:
            rotated_key: Final[KeyData | None] = (
                await session.execute(statement)
            ).scalar_one_or_none()
            if not rotated_key or not (
                include_expired or rotated_key.expired_at is None
            ):
                return None
            if rotated_key.expired_at is None:
                return RotatedKeyDataResult.construct_from_orm(rotated_key)
            return ExpiredKeyDataResult.construct_from_orm(rotated_key)

    @overload
    async def get_relevant_keydata(
        self,
        *,
        active_key_public_data_only: Literal[False] = False,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> tuple[ActiveKeyPrivateDataResult, list[RotatedKeyDataResult]]: ...

    @overload
    async def get_relevant_keydata(
        self,
        *,
        active_key_public_data_only: Literal[True] = True,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> tuple[ActiveKeyPublicDataResult, list[RotatedKeyDataResult]]: ...

    @overload
    async def get_relevant_keydata(
        self,
        *,
        active_key_public_data_only: Literal[False] = False,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> tuple[ActiveKeyPrivateDataResult, list[RotatedKeyDataResult]]: ...

    async def get_relevant_keydata(
        self,
        *,
        active_key_public_data_only: bool = True,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> (
        tuple[ActiveKeyPublicDataResult, list[RotatedKeyDataResult]]
        | tuple[ActiveKeyPrivateDataResult, list[RotatedKeyDataResult]]
    ):
        statement = (
            select(KeyData)
            .where(KeyData.expired_at.is_(None))
            .order_by(KeyData.epoch.desc())
        )

        if lock_args:
            statement = statement.with_for_update(
                **{lock_arg: True for lock_arg in lock_args}  # pyrefly: ignore
            )

        async with self._work_scoped_session() as session:
            keydata: Sequence[KeyData] = (
                (await session.execute(statement)).scalars().all()
            )
            if not keydata:
                raise EmptyKeysError("No relevant keydata found")
            active_private_keydata: ActiveKeyPrivateDataResult = (
                ActiveKeyPrivateDataResult.construct_from_orm(keydata[0])
            )
            rotated_keydata: Final[list[RotatedKeyDataResult]] = [
                RotatedKeyDataResult.construct_from_orm(k) for k in keydata[1:]
            ]
            if active_key_public_data_only:
                return active_private_keydata.create_public_copy(), rotated_keydata
            return active_private_keydata, rotated_keydata

    @overload
    async def insert_keydata(
        self,
        key_id: str,
        private_key_bytes: bytes,
        public_key_bytes: bytes,
        alg: str,
        curve: ec.EllipticCurve,
        epoch: datetime | None = None,
        *,
        returning: Literal[False] = False,
    ) -> None: ...

    @overload
    async def insert_keydata(
        self,
        key_id: str,
        private_key_bytes: bytes,
        public_key_bytes: bytes,
        alg: str,
        curve: ec.EllipticCurve,
        epoch: datetime | None = None,
        *,
        returning: Literal[True] = True,
    ) -> ActiveKeyPrivateDataResult: ...

    async def insert_keydata(
        self,
        key_id: str,
        private_key_bytes: bytes,
        public_key_bytes: bytes,
        alg: str,
        curve: ec.EllipticCurve,
        epoch: datetime | None = None,
        *,
        returning: bool = False,
    ) -> ActiveKeyPrivateDataResult | None:
        async with self._work_scoped_session() as session:
            keydata: KeyData = (
                await session.execute(
                    insert(KeyData)
                    .values(
                        kid=key_id,
                        alg=alg,
                        curve=curve.name,
                        epoch=epoch or datetime.now(UTC),
                        private_pem=private_key_bytes,
                        public_pem=public_key_bytes,
                    )
                    .returning(KeyData)
                )
            ).scalar_one()

            if returning:
                return ActiveKeyPrivateDataResult.construct_from_orm(keydata)

    @overload
    async def expire_keydata_with_threshold(
        self,
        threshold: datetime,
        expiry_time: datetime | None = None,
        *,
        return_expired: Literal[False] = False,
    ) -> None: ...
    @overload
    async def expire_keydata_with_threshold(
        self,
        threshold: datetime,
        expiry_time: datetime | None = None,
        *,
        return_expired: Literal[True] = True,
    ) -> list[ExpiredKeyDataResult]: ...

    async def expire_keydata_with_threshold(
        self,
        threshold: datetime,
        expiry_time: datetime | None = None,
        *,
        return_expired: bool = False,
    ) -> list[ExpiredKeyDataResult] | None:
        async with self._work_scoped_session() as session:
            expired_keys: list[KeyData] = list(
                (
                    await session.execute(
                        update(KeyData)
                        .where(
                            (KeyData.epoch < threshold) & (KeyData.expired_at.is_(None))
                        )
                        .values(expired_at=expiry_time or datetime.now(UTC))
                        .returning(KeyData)
                    )
                )
                .scalars()
                .all()
            )

            if return_expired:
                return list(map(ExpiredKeyDataResult.construct_from_orm, expired_keys))

    @overload
    async def expire_keydata(
        self,
        kid: str,
        expiry_time: datetime | None = None,
        *,
        return_expired: Literal[False] = False,
    ) -> None: ...
    @overload
    async def expire_keydata(
        self,
        kid: str,
        expiry_time: datetime | None = None,
        *,
        return_expired: Literal[True] = True,
    ) -> ExpiredKeyDataResult | None: ...

    async def expire_keydata(
        self,
        kid: str,
        expiry_time: datetime | None = None,
        *,
        return_expired: bool = False,
    ) -> ExpiredKeyDataResult | None:
        async with self._work_scoped_session() as session:
            expired_key: KeyData | None = (
                await session.execute(
                    update(KeyData)
                    .where((KeyData.kid == kid) & (KeyData.expired_at.is_(None)))
                    .values(expired_at=expiry_time or datetime.now(UTC))
                    .returning(KeyData)
                )
            ).scalar_one_or_none()

            if not expired_key:
                return None

            if return_expired:
                return ExpiredKeyDataResult.construct_from_orm(expired_key)

    @overload
    async def batch_expire_keydata(
        self,
        kids: Sequence[str],
        expiry_time: datetime | None = None,
        *,
        return_expired: Literal[False] = False,
    ) -> None: ...
    @overload
    async def batch_expire_keydata(
        self,
        kids: Sequence[str],
        expiry_time: datetime | None = None,
        *,
        return_expired: Literal[True] = True,
    ) -> list[ExpiredKeyDataResult]: ...

    async def batch_expire_keydata(
        self,
        kids: Sequence[str],
        expiry_time: datetime | None = None,
        *,
        return_expired: bool = False,
    ) -> list[ExpiredKeyDataResult] | None:
        async with self._work_scoped_session() as session:
            expired_keys: list[KeyData] = list(
                (
                    await session.execute(
                        update(KeyData)
                        .where((KeyData.kid.in_(kids)) & (KeyData.expired_at.is_(None)))
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
                return list(map(ExpiredKeyDataResult.construct_from_orm, expired_keys))

    async def get_expired_keys(
        self, *, limit: int | None = None, offset_id: str | None = None
    ) -> list[ExpiredKeyDataResult]:
        selection_clauses: list[ColumnExpressionArgument] = [
            KeyData.expired_at.isnot(None)
        ]
        if offset_id is not None:
            selection_clauses.append(KeyData.kid > offset_id)
        async with self._work_scoped_session() as session:
            expired_keys: list[KeyData] = list(
                (
                    await session.execute(
                        select(KeyData)
                        .where(and_(*selection_clauses))
                        .limit(limit)
                        .order_by(KeyData.expired_at)
                    )
                )
                .scalars()
                .all()
            )
            return list(map(ExpiredKeyDataResult.construct_from_orm, expired_keys))

    async def get_valid_inactive_keys(
        self,
        *,
        limit: int | None = None,
        offset_id: str | None = None,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> list[RotatedKeyDataResult]:
        selection_clauses: list[ColumnExpressionArgument] = [
            KeyData.expired_at.is_(None),
            KeyData.rotated_out_at.isnot(None),
        ]
        if offset_id is not None:
            selection_clauses.append(KeyData.kid > offset_id)
        statement = (
            select(KeyData)
            .where(and_(*selection_clauses))
            .order_by(KeyData.epoch.desc())
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
            return list(map(RotatedKeyDataResult.construct_from_orm, keys))

    @overload
    async def get_active_key(
        self,
        *,
        public_data_only: Literal[True] = True,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> ActiveKeyPrivateDataResult: ...
    @overload
    async def get_active_key(
        self,
        *,
        public_data_only: Literal[False] = False,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> ActiveKeyPublicDataResult: ...

    async def get_active_key(
        self,
        *,
        public_data_only: bool = True,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> ActiveKeyPublicDataResult | ActiveKeyPrivateDataResult | None:
        statement = select(KeyData).where(
            (KeyData.rotated_out_at.is_(None)) & (KeyData.expired_at.is_(None))
        )
        if lock_args:
            statement = statement.with_for_update(
                **{i: True for i in lock_args}  # pyrefly: ignore
            )

        async with self._work_scoped_session() as session:
            active_key: KeyData | None = (
                await session.execute(statement)
            ).scalar_one_or_none()
            if not active_key:
                raise EmptyKeysError("No active key found")
            if public_data_only:
                return ActiveKeyPublicDataResult.construct_from_orm(active_key)
            return ActiveKeyPrivateDataResult.construct_from_orm(active_key)

    @overload
    async def rotate_key(
        self,
        previous_key_id: str,
        new_key_id: str,
        new_key_public_pem: bytes | bytearray,
        new_key_private_pem: bytes | bytearray,
        *,
        alg: str = "ES256",
        curve: type[ec.EllipticCurve] = ec.SECP256R1,
        rotation_author: int | None = None,
        epoch: datetime | None = None,
        previous_key_rotation_time: datetime | None = None,
        returning: Literal[False] = False,
    ) -> None: ...

    @overload
    async def rotate_key(
        self,
        previous_key_id: str,
        new_key_id: str,
        new_key_public_pem: bytes | bytearray,
        new_key_private_pem: bytes | bytearray,
        *,
        alg: str = "ES256",
        curve: type[ec.EllipticCurve] = ec.SECP256R1,
        rotation_author: int | None = None,
        epoch: datetime | None = None,
        previous_key_rotation_time: datetime | None = None,
        returning: Literal[True],
    ) -> ActiveKeyPublicDataResult: ...

    async def rotate_key(
        self,
        previous_key_id: str,
        new_key_id: str,
        new_key_public_pem: bytes | bytearray,
        new_key_private_pem: bytes | bytearray,
        *,
        alg: str = "ES256",
        curve: type[ec.EllipticCurve] = ec.SECP256R1,
        rotation_author: int | None = None,
        epoch: datetime | None = None,
        previous_key_rotation_time: datetime | None = None,
        returning: bool = False,
    ) -> ActiveKeyPublicDataResult | None:
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
                    private_pem=None,
                )
            )

            # Add new key
            new_key: Final[KeyData] = (
                await session.execute(
                    insert(KeyData)
                    .values(
                        kid=new_key_id,
                        curve=curve.name,
                        private_pem=new_key_private_pem,
                        public_pem=new_key_public_pem,
                        alg=alg,
                        epoch=epoch,
                    )
                    .returning(KeyData)
                )
            ).scalar_one()

            if returning:
                return ActiveKeyPublicDataResult.construct_from_orm(new_key)

    async def get_jwks(self) -> tuple[EllipticCurveJWKResult, ...]:
        active, rotated = await self.get_relevant_keydata()
        return (active.as_jwk(), *(r.as_jwk() for r in rotated))

    async def get_active_private_pem(
        self, *, active_key_id: str | None = None
    ) -> bytes:
        statement = select(KeyData.private_pem).where(
            KeyData.kid == active_key_id
            if active_key_id is not None
            else KeyData.rotated_out_at.is_(None)
        )
        async with self._work_scoped_session() as session:
            try:
                private_pem: bytes | None = (
                    await session.execute(statement)
                ).scalar_one()
                if private_pem is None:  # Should never happen!!!
                    raise ValueError(
                        "Invalid database state, active key has missing private PEM bytes"
                    )
                return private_pem
            except SQLAlchemyError as e:
                raise EmptyKeysError("No active key found") from e
