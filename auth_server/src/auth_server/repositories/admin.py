"""Data access repository for Keydata SA model"""

from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal, overload

from auxillary.data_structures.dto import AbstractResult
from auxillary.data_structures.repository import AbstractWorkRepository
from redis.typing import EncodableT, FieldT
from sqlalchemy import insert, select, update

from auth_server.models.database import Admin
from auth_server.security.admin_roles import AdminRole
from auth_server.strings import SelectionLockOption


@dataclass(slots=True, init=False)
class AdminPublicResult(AbstractResult):
    """
    DTO for an Admin ORM object, excluding the private signing key.
    """

    resource_name: ClassVar[str] = Admin.__tablename__

    id_: int
    username: str
    role: str  # TODO: Add roles enum

    password_hash: bytes

    time_deleted: datetime | None
    last_login: datetime
    locked: bool
    created_by: int

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


@dataclass(slots=True)
class AdminRepository(AbstractWorkRepository):
    async def get_admin(
        self,
        admin_id: int,
        *,
        include_deleted: bool = False,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> AdminPublicResult | None:
        statement = select(Admin).where(Admin.id_ == admin_id)
        if not include_deleted:
            statement = statement.where(Admin.time_deleted.is_(None))
        if lock_args:
            statement = statement.with_for_update(
                **{lock_arg: True for lock_arg in lock_args}  # pyrefly: ignore
            )

        async with self._work_scoped_session() as session:
            admin: Admin | None = (
                await session.execute(statement)
            ).scalar_one_or_none()
            if not admin:
                return None
            return AdminPublicResult.construct_from_orm(admin)

    async def get_admin_by_username(
        self,
        username: str,
        *,
        include_deleted: bool = False,
        lock_args: Sequence[SelectionLockOption] | None = None,
    ) -> AdminPublicResult | None:
        statement = select(Admin).where(Admin.username == username)
        if not include_deleted:
            statement = statement.where(Admin.time_deleted.is_(None))
        if lock_args:
            statement = statement.with_for_update(
                **{lock_arg: True for lock_arg in lock_args}  # pyrefly: ignore
            )

        async with self._work_scoped_session() as session:
            admin: Admin | None = (
                await session.execute(statement)
            ).scalar_one_or_none()
            if not admin:
                return None
            return AdminPublicResult.construct_from_orm(admin)

    @overload
    async def insert_admin(
        self,
        username: str,
        password_hash: bytes,
        role: str,
        created_by: int,
        signing_key: bytes,
        verification_key: bytes,
        *,
        returning: Literal[False] = False,
    ) -> None: ...

    @overload
    async def insert_admin(
        self,
        username: str,
        password_hash: bytes,
        role: str,
        created_by: int,
        signing_key: bytes,
        verification_key: bytes,
        *,
        returning: Literal[True],
    ) -> AdminPublicResult: ...

    async def insert_admin(
        self,
        username: str,
        password_hash: bytes,
        role: str,
        created_by: int,
        signing_key: bytes,
        verification_key: bytes,
        *,
        returning: bool = False,
    ) -> AdminPublicResult | None:
        async with self._work_scoped_session() as session:
            admin: Admin = (
                await session.execute(
                    insert(Admin)
                    .values(
                        username=username,
                        password_hash=password_hash,
                        role=role,
                        created_by=created_by,
                        signing_key=signing_key,
                        verification_key=verification_key,
                    )
                    .returning(Admin)
                )
            ).scalar_one()

            if returning:
                return AdminPublicResult.construct_from_orm(admin)
            return None

    async def update_last_login(
        self, admin_id: int, login_time: datetime | None = None
    ) -> None:
        async with self._work_scoped_session() as session:
            await session.execute(
                update(Admin)
                .where(Admin.id_ == admin_id)
                .values(last_login=login_time or datetime.now(UTC))
            )

    @overload
    async def delete_admin(
        self,
        admin_id: int,
        deletion_time: datetime | None = None,
        *,
        returning: Literal[False] = False,
    ) -> None: ...

    @overload
    async def delete_admin(
        self,
        admin_id: int,
        deletion_time: datetime | None = None,
        *,
        returning: Literal[True],
    ) -> AdminPublicResult | None: ...

    async def delete_admin(
        self,
        admin_id: int,
        deletion_time: datetime | None = None,
        *,
        returning: bool = False,
    ) -> AdminPublicResult | None:
        async with self._work_scoped_session() as session:
            admin: Admin | None = (
                await session.execute(
                    update(Admin)
                    .where((Admin.id_ == admin_id) & (Admin.time_deleted.is_(None)))
                    .values(time_deleted=deletion_time or datetime.now(UTC))
                    .returning(Admin)
                )
            ).scalar_one_or_none()

            if returning and admin:
                return AdminPublicResult.construct_from_orm(admin)
            return None

    @overload
    async def set_admin_locked(
        self,
        admin_id: int,
        locked: bool,
        *,
        returning: Literal[False] = False,
    ) -> None: ...

    @overload
    async def set_admin_locked(
        self,
        admin_id: int,
        locked: bool,
        *,
        returning: Literal[True],
    ) -> AdminPublicResult | None: ...

    async def set_admin_locked(
        self,
        admin_id: int,
        locked: bool,
        *,
        returning: bool = False,
    ) -> AdminPublicResult | None:
        async with self._work_scoped_session() as session:
            admin: Admin | None = (
                await session.execute(
                    update(Admin)
                    .where(Admin.id_ == admin_id)
                    .values(locked=locked)
                    .returning(Admin)
                )
            ).scalar_one_or_none()

            if returning and admin:
                return AdminPublicResult.construct_from_orm(admin)
            return None

    @overload
    async def create_admin(
        self,
        username: str,
        password_hash: bytes | bytearray,
        creation_author: int,
        signing_key: bytes | bytearray,
        verification_key: bytes | bytearray,
        role: AdminRole = AdminRole.STAFF,
        *,
        returning: Literal[False] = False,
    ) -> None: ...

    @overload
    async def create_admin(
        self,
        username: str,
        password_hash: bytes | bytearray,
        creation_author: int,
        signing_key: bytes | bytearray,
        verification_key: bytes | bytearray,
        role: AdminRole = AdminRole.STAFF,
        *,
        returning: Literal[True],
    ) -> AdminPublicResult: ...

    async def create_admin(
        self,
        username: str,
        password_hash: bytes | bytearray,
        creation_author: int,
        signing_key: bytes | bytearray,
        verification_key: bytes | bytearray,
        role: AdminRole = AdminRole.STAFF,
        *,
        returning: bool = False,
    ) -> AdminPublicResult | None:
        async with self._work_scoped_session() as session:
            admin: Admin = (
                await session.execute(
                    insert(Admin)
                    .values(
                        username=username,
                        password_hash=password_hash,
                        role=role,
                        created_by=creation_author,
                        signing_key=signing_key,
                        verification_key=verification_key,
                    )
                    .returning(Admin)
                )
            ).scalar_one()

            if returning:
                return AdminPublicResult.construct_from_orm(admin)
            return None
