from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from resource_server.repositories.result_protocol import AbstractResult
from resource_server.models.database import User
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


@dataclass(slots=True, weakref_slot=True)
class UserRepository(metaclass=SingletonMetaclass):
    session_maker: async_sessionmaker[AsyncSession]

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
