from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Literal, Mapping, Self, overload

from sqlalchemy import ColumnElement, Row, and_, delete, insert, select, update
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


@dataclass(slots=True, init=False)
class ForumAdminUserResult(UserResult):
    role: AdminRoles

    @classmethod
    def construct_from_cache(cls, mapping: Mapping[str, Any]) -> Self:
        instance = super().construct_from_cache(mapping)
        instance.role = AdminRoles(mapping["role"])
        return instance

    @classmethod
    def construct_from_orm(
        cls, obj: User, role: str | AdminRoles, *args, **kwargs
    ) -> Self:
        instance = super().construct_from_orm(obj, *args, **kwargs)
        if not isinstance(role, AdminRoles):
            role = AdminRoles(role)
        instance.role = role
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

    async def add_forum_admin(
        self,
        forum_id: int,
        user_id: int,
        role: Literal[AdminRoles.SUPER, AdminRoles.ADMIN],
    ) -> None:
        async with self.session_maker() as session:
            await session.execute(
                insert(ForumAdmin).values(
                    forum_id=forum_id, user_id=user_id, role=role.value
                )
            )
            await session.commit()

    async def remove_forum_admin(self, forum_id: int, admin_id: int) -> None:
        async with self.session_maker() as session:
            await session.execute(
                delete(ForumAdmin).where(
                    (ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == admin_id)
                )
            )
            await session.commit()

    async def get_forum_admin_users(
        self, forum_id: int, cursor: int = 0, limit: int | None = None
    ) -> list[ForumAdminUserResult]:
        async with self.session_maker() as session:
            forum_admin_users: list[Row[tuple[User, str]]] = list(
                (
                    await session.execute(
                        select(User, ForumAdmin.role)
                        .join(ForumAdmin, ForumAdmin.user_id == User.id_)
                        .where((ForumAdmin.forum_id == forum_id) & (User.id_ > cursor))
                        .limit(limit)
                    )
                ).all()
            )

            return [
                ForumAdminUserResult.construct_from_orm(*i) for i in forum_admin_users
            ]

    async def update_admin_role(
        self, forum_id: int, admin_id: int, role: AdminRoles
    ) -> None:
        async with self.session_maker() as session:
            await session.execute(
                update(ForumAdmin)
                .where(
                    (ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == admin_id)
                )
                .values(role=role.value)
            )
            await session.commit()
