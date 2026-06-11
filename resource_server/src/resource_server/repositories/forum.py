from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any, ClassVar, Literal, Mapping, Self, overload

from sqlalchemy import ColumnElement, and_, delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from resource_server.repositories.user import UserResult
from resource_server.utils.singleton import SingletonMetaclass
from resource_server.repositories.result_protocol import AbstractResult
from resource_server.models.database import Forum, ForumAdmin, User, AdminRoles

from resource_auxillary.strings import NAME_SEPERATOR


@dataclass(slots=True, init=False)
class ForumResult(AbstractResult):
    id_: int
    name_: str
    anime: int

    description: str | None = None

    subscribers: int
    posts: int
    created_at: datetime
    admin_count: int

    COUNTER_FIELDS: ClassVar[tuple[str, ...]] = ("subscribers", "posts")


@dataclass(slots=True, init=False)
class ForumAdminResult(AbstractResult):
    forum_id: int
    user_id: int
    role: AdminRoles

    COUNTER_FIELDS: ClassVar[tuple[str, ...]] = tuple()

    @lru_cache(maxsize=1)
    @classmethod
    def get_counter_fields(cls) -> dict[str, str]:
        return {
            i: NAME_SEPERATOR.join((Forum.__tablename__, i)) for i in cls.COUNTER_FIELDS
        }

    @classmethod
    def construct_from_cache(cls, mapping: Mapping[str, Any]) -> Self:
        instance = cls()
        instance.user_id = mapping["user_id"]
        instance.forum_id = mapping["forum_id"]
        instance.role = AdminRoles(mapping["role"])

        return instance

    @classmethod
    def construct_from_orm(
        cls,
        obj: ForumAdmin,
        *args,
        **kwargs,
    ) -> Self:
        instance = cls()
        instance.user_id = obj.user_id
        instance.forum_id = obj.forum_id
        instance.role = AdminRoles(obj.role)

        return instance


@dataclass(slots=True, weakref_slot=True)
class ForumRepository(metaclass=SingletonMetaclass):
    session_maker: async_sessionmaker[AsyncSession]

    async def get_forum(self, forum_id: int) -> ForumResult | None:
        async with self.session_maker() as session:
            forum: Forum | None = (
                await session.execute(select(Forum).where(Forum.id_ == forum_id))
            ).scalar_one_or_none()

            if not forum:
                return None

            return ForumResult.construct_from_orm(forum)

    async def get_forum_by_name(self, name: str) -> ForumResult | None:
        async with self.session_maker() as session:
            forum: Forum | None = (
                await session.execute(select(Forum).where(Forum.name_ == name))
            ).scalar_one_or_none()

            if not forum:
                return None

            return ForumResult.construct_from_orm(forum)

    async def get_forums(
        self,
        cursor: int = 0,
        search_param: str | None = None,
        parent_anime_id: int | None = None,
    ) -> list[ForumResult]:
        where_clauses: list[ColumnElement] = [Forum.id_ > cursor]
        if search_param:
            where_clauses.append(Forum.name_.ilike(f"%{search_param}%"))
        if parent_anime_id:
            where_clauses.append(Forum.anime == parent_anime_id)

        async with self.session_maker() as session:
            forums: list[Forum] = list(
                (await session.execute(select(Forum).where(and_(*where_clauses))))
                .scalars()
                .all()
            )

            return [ForumResult.construct_from_orm(f) for f in forums]

    async def create_forum(
        self,
        name: str,
        description: str,
        parent_anime_id: int,
        creator_id: int,
        creation_time: datetime | None = None,
    ) -> ForumResult:
        async with self.session_maker() as session:
            forum: Forum = (
                await session.execute(
                    insert(Forum)
                    .values(
                        name_=name,
                        desscription=description,
                        anime=parent_anime_id,
                        created_at=creation_time,
                    )
                    .returning(Forum)
                )
            ).scalar_one()

            await session.flush()
            await session.execute(
                insert(ForumAdmin).values(
                    forum_id=forum.id_, user_id=creator_id, role=AdminRoles.OWNER
                )
            )
            await session.commit()
            return ForumResult.construct_from_orm(forum)

    async def get_forum_owner(self, forum_id: int) -> UserResult:
        async with self.session_maker() as session:
            user: User = (
                await session.execute(
                    select(User)
                    .join(ForumAdmin, ForumAdmin.forum_id == Forum.id_)
                    .join(User, User.id_ == ForumAdmin.user_id)
                    .where(
                        (Forum.id_ == forum_id)
                        & (ForumAdmin.role == AdminRoles.OWNER.value)
                    )
                )
            ).scalar_one()

            return UserResult.construct_from_orm(user)

    async def get_forum_admin(
        self, forum_id: int, user_id: int
    ) -> ForumAdminResult | None:
        async with self.session_maker() as session:
            admin: ForumAdmin | None = (
                await session.execute(
                    select(ForumAdmin).where(
                        (ForumAdmin.forum_id == forum_id)
                        & (ForumAdmin.user_id == user_id)
                    )
                )
            ).scalar_one_or_none()

            if not admin:
                return None

            return ForumAdminResult.construct_from_orm(admin)

    @overload
    async def update_forum(
        self,
        forum_id: int,
        title: str | None,
        description: str | None,
        *,
        return_forum: Literal[False],
    ) -> None: ...

    @overload
    async def update_forum(
        self,
        forum_id: int,
        title: str | None,
        description: str | None,
        *,
        return_forum: Literal[True],
    ) -> ForumResult: ...

    @overload
    async def update_forum(
        self,
        forum_id: int,
        title: str | None,
        description: str | None,
    ) -> None: ...

    async def update_forum(
        self,
        forum_id: int,
        title: str | None,
        description: str | None,
        *,
        return_forum: bool = False,
    ) -> ForumResult | None:
        if not (title or description):
            raise ValueError("Empty updation requested")
        update_clauses: dict[str, str] = {}
        if title:
            update_clauses["title"] = title
        if description:
            update_clauses["description"] = description

        async with self.session_maker() as session:
            forum: Forum = (
                await session.execute(
                    update(Forum)
                    .where(Forum.id_ == forum_id)
                    .values(**update_clauses)
                    .returning(Forum)
                )
            ).scalar_one()

            await session.commit()

            if return_forum:
                return ForumResult.construct_from_orm(forum)
