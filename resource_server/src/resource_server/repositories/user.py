from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Mapping, Never, Self

from sqlalchemy import ColumnElement, Row, and_, delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from resource_server.repositories.result_protocol import AbstractResult
from resource_server.models.database import PasswordRecoveryToken, User
from resource_server.utils.singleton import SingletonMetaclass

from resource_auxillary.strings import NAME_SEPERATOR


@dataclass(slots=True, init=False)
class UserResult(AbstractResult):
    id_: int
    username: str

    aura: int
    total_posts: int
    total_comments: int

    time_joined: datetime
    last_login: datetime

    COUNTER_FIELDS: ClassVar[tuple[str, ...]] = (
        "aura",
        "total_posts",
        "total_comments",
    )
    resource_name: ClassVar[str] = User.__tablename__


@dataclass(slots=True, init=False)
class PrivateUserResult(UserResult):
    pw_hash: bytes
    deleted: bool
    time_deleted: datetime
    rtbf: bool

    @classmethod
    def construct_from_cache(cls, mapping: Mapping[str, Any], *args, **kwargs) -> Never:
        raise RuntimeError("Cache mapping of private user data violates policy")

    def __cache_repr__(self) -> Never:
        raise RuntimeError("Cannot create cache representation of private user data")

    @classmethod
    def construct_from_orm(cls, obj: User, *args, **kwargs) -> Self:
        instance = super().construct_from_orm(obj, *args, **kwargs)
        instance.pw_hash = obj.pw_hash
        instance.deleted = obj.deleted
        instance.time_deleted = obj.time_deleted
        instance.rtbf = obj.rtbf

        return instance


@dataclass(slots=True, weakref_slot=True)
class UserRepository(metaclass=SingletonMetaclass):
    session_maker: async_sessionmaker[AsyncSession]

    async def add_user(
        self, username: str, email: str, password: bytes | bytearray
    ) -> UserResult:
        async with self.session_maker() as session:
            user: User = (
                await session.execute(
                    insert(User).values(
                        username=username, email=email, pw_hash=password
                    )
                )
            ).scalar_one()
            await session.commit()

            return UserResult.construct_from_orm(user)

    async def get_user(self, user_id: int) -> UserResult | None:
        async with self.session_maker() as session:
            user: User | None = (
                await session.execute(
                    select(User).where(
                        (User.id_ == user_id) & (User.deleted.is_(False))
                    )
                )
            ).scalar_one_or_none()

            if not user:
                return None

            return UserResult.construct_from_orm(user)

    async def get_user_by_identity(self, username: str, email: str) -> list[UserResult]:
        async with self.session_maker() as session:
            users: list[User] = list(
                (
                    await session.execute(
                        select(User).where(
                            (User.username == username) | (User.email == email)
                        )
                    )
                )
                .scalars()
                .all()
            )

            return [UserResult.construct_from_orm(user) for user in users]

    async def get_user_by_username(
        self,
        username: str,
    ) -> UserResult | None:
        async with self.session_maker() as session:
            user: User | None = (
                await session.execute(
                    select(User).where(
                        (User.username == username) & (User.deleted.is_(False))
                    )
                )
            ).scalar_one_or_none()

            if not user:
                return None

            return UserResult.construct_from_orm(user)

    async def get_user_by_email(
        self,
        email: str,
    ) -> UserResult | None:
        async with self.session_maker() as session:
            user: User | None = (
                await session.execute(
                    select(User).where(
                        (User.email == email) & (User.deleted.is_(False))
                    )
                )
            ).scalar_one_or_none()

            if not user:
                return None

            return UserResult.construct_from_orm(user)

    async def get_rtbf(self, user_id: int) -> bool:
        async with self.session_maker() as session:
            return (
                await session.execute(select(User.rtbf).where(User.id_ == user_id))
            ).scalar_one()

    async def update_rtbf(self, user_id: int, rtbf: bool) -> None:
        async with self.session_maker() as session:
            await session.execute(
                update(User).where(User.id_ == user_id).values(rtbf=rtbf)
            )
            await session.commit()

    async def set_password_recovery_token(
        self, user_id: int, url_hash: str, expiry: datetime
    ) -> None:
        async with self.session_maker() as session:
            await session.execute(
                delete(PasswordRecoveryToken).where(
                    PasswordRecoveryToken.user_id == user_id
                )
            )
            await session.execute(
                insert(PasswordRecoveryToken).values(
                    user_id=user_id, expiry=expiry, url_hash=url_hash
                )
            )
            await session.commit()

    async def get_user_password_recovery_token(
        self, user_id: int
    ) -> tuple[UserResult | None, tuple[str, datetime] | tuple[None, None]]:
        async with self.session_maker() as session:
            res: Row[tuple[User, str, datetime]] | None = (
                await session.execute(
                    select(
                        User,
                        PasswordRecoveryToken.url_hash,
                        PasswordRecoveryToken.expiry,
                    )
                    .select_from(User)
                    .outerjoin(
                        PasswordRecoveryToken, PasswordRecoveryToken.user_id == User.id_
                    )
                    .where(User.id_ == user_id)
                )
            ).first()

            if not res:
                return None, (None, None)

            user, url_hash, expiry = res.tuple()

            return UserResult.construct_from_orm(user), (url_hash, expiry)

    async def update_password(
        self, user_id: int, password_hash: bytes | bytearray
    ) -> None:
        async with self.session_maker() as session:
            await session.execute(
                update(User).where(User.id_ == user_id).values(pw_hash=password_hash)
            )

            await session.execute(
                delete(PasswordRecoveryToken).where(User.id_ == user_id)
            )

            await session.commit()

    async def get_user_password(self, user_id: int) -> bytes:
        async with self.session_maker() as session:
            return (
                await session.execute(select(User.pw_hash).where(User.id_ == user_id))
            ).scalar_one()

    async def delete_user(
        self, user_id: int, *, deletion_time: datetime | None = None
    ) -> None:
        deletion_time = deletion_time or datetime.now()
        async with self.session_maker() as session:
            await session.execute(
                update(User)
                .where(User.id_ == user_id)
                .values(deleted=True, time_deleted=deletion_time)
            )

            await session.commit()

    async def get_full_user_profile(
        self, username: str, *, skip_deleted: bool = False
    ) -> PrivateUserResult | None:
        where_clauses: list[ColumnElement] = [User.username == username]
        if skip_deleted:
            where_clauses.append(User.deleted.is_(False))

        async with self.session_maker() as session:
            user: User | None = (
                await session.execute(select(User).where(and_(*where_clauses)))
            ).scalar_one_or_none()

            if not user:
                return None

            return PrivateUserResult.construct_from_orm(user)

    async def recover_user(self, user_id: int) -> None:
        async with self.session_maker() as session:
            await session.execute(
                update(User)
                .where(User.id_ == user_id)
                .values(deleted=False, time_deleted=None)
            )
            await session.commit()
