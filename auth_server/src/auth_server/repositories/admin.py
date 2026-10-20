"""Data access repository for Keydata SA model"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar, Literal, overload

from auxillary.data_structures.dto import AbstractResult
from auxillary.data_structures.repository import AbstractWorkRepository
from sqlalchemy import insert, select, update

from auth_server.admin.roles import AdminRole
from auth_server.models.database import Admin
from auth_server.strings import SelectionLockOption


@dataclass(slots=True, init=False)
class AdminPublicResult(AbstractResult):
    """
    DTO for an Admin ORM object, excluding the private signing key.
    """

    resource_name: ClassVar[str] = Admin.__tablename__

    id_: int
    username: str
    role: AdminRole

    time_deleted: datetime | None
    last_login: datetime
    locked: bool
    created_by: int


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

    @overload
    async def get_admin_by_username(
        self,
        username: str,
        *,
        include_deleted: bool = False,
        lock_args: Sequence[SelectionLockOption] | None = None,
        include_password_hash: Literal[False] = False,
    ) -> AdminPublicResult | None: ...

    @overload
    async def get_admin_by_username(
        self,
        username: str,
        *,
        include_deleted: bool = False,
        lock_args: Sequence[SelectionLockOption] | None = None,
        include_password_hash: Literal[True] = True,
    ) -> tuple[AdminPublicResult, bytes] | None: ...

    async def get_admin_by_username(
        self,
        username: str,
        *,
        include_deleted: bool = False,
        lock_args: Sequence[SelectionLockOption] | None = None,
        include_password_hash: bool = False,
    ) -> tuple[AdminPublicResult, bytes] | AdminPublicResult | None:
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
            if include_password_hash:
                return AdminPublicResult.construct_from_orm(admin), admin.password_hash
            return AdminPublicResult.construct_from_orm(admin)

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
        role: AdminRole = AdminRole.STAFF,
        *,
        returning: Literal[True],
    ) -> AdminPublicResult: ...

    async def create_admin(
        self,
        username: str,
        password_hash: bytes | bytearray,
        creation_author: int,
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
                    )
                    .returning(Admin)
                )
            ).scalar_one()

            if returning:
                return AdminPublicResult.construct_from_orm(admin)
            return None
