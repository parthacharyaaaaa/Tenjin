from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any, ClassVar, Mapping, Self

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

    @classmethod
    @lru_cache(maxsize=1)
    def get_counter_fields(cls) -> dict[str, str]:
        return {
            field: NAME_SEPERATOR.join((User.__tablename__, field))
            for field in cls.COUNTER_FIELDS
        }

    @classmethod
    def construct_from_cache(cls, mapping: Mapping[str, Any]) -> Self:
        instance = cls()

        instance.id_ = mapping["id"]
        instance.username = mapping["username"]

        instance.aura = mapping["aura"]
        instance.total_posts = mapping["total_posts"]
        instance.total_comments = mapping["total_comments"]

        instance.time_joined = mapping["time_joined"]
        instance.last_login = mapping["last_login"]

        return instance

    @classmethod
    def construct_from_orm(
        cls,
        obj: User,
        *args,
        **kwargs,
    ) -> Self:
        instance = cls()

        instance.id_ = obj.id_
        instance.username = obj.username

        instance.aura = obj.aura
        instance.total_posts = obj.total_posts
        instance.total_comments = obj.total_comments

        instance.time_joined = obj.time_joined
        instance.last_login = obj.last_login

        return instance


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
