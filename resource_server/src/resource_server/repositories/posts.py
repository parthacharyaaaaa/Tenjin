from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Mapping, Self

from sqlalchemy import Row, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from resource_server.datastructures.requests import SortOption
from resource_server.utils.singleton import SingletonMetaclass
from resource_server.repositories.result_protocol import AbstractResult
from resource_server.models.database import Post, PostReport, PostSave, PostVote, User
from resource_server.models.database_enums import ReportTags

from resource_auxillary.strings import NAME_SEPERATOR


@dataclass(slots=True, init=False)
class PostResult(AbstractResult):
    id_: int
    author_id: int
    forum_id: int
    author_username: str

    # Post statistics
    score: int
    total_comments: int
    saves: int
    reports: int

    # Post details
    title: str
    body_text: str

    flair: str | None
    closed: bool
    time_posted: datetime

    COUNTER_FIELDS: ClassVar[tuple[str, ...]] = ("saves", "reports", "total_comments")
    resource_name: ClassVar[str] = Post.__tablename__

    @classmethod
    def construct_from_cache(cls, mapping: Mapping[str, Any]) -> Self:
        instance = super().construct_from_cache(mapping)
        instance.author_username = mapping["author_username"]
        return instance

    @classmethod
    def construct_from_orm(
        cls, obj: DeclarativeBase, author_username: str, *args, **kwargs
    ) -> Self:
        instance = super().construct_from_orm(obj, *args, **kwargs)
        instance.author_username = author_username
        return instance


@dataclass(slots=True, weakref_slot=True)
class PostRepository(metaclass=SingletonMetaclass):
    session_maker: async_sessionmaker[AsyncSession]

    async def get_post(self, post_id: int) -> PostResult | None:
        async with self.session_maker() as session:
            result: Row[tuple[Post, str]] | None = (
                await session.execute(
                    select(Post, User.username)
                    .join(User, User.id_ == Post.author_id)
                    .where((Post.id_ == post_id) & (Post.deleted == False))
                )
            ).first()
            if not result:
                return None
            return PostResult.construct_from_orm(*result.tuple())

    async def update_post(
        self,
        post_id: int,
        post_title: str | None = None,
        post_body: str | None = None,
        closed: bool = False,
    ) -> None:
        update_kw: dict[str, Any] = {}
        if post_title:
            update_kw["title"] = post_title
        if post_body:
            update_kw["body"] = post_body
        if closed:
            update_kw["closed"] = True

        if not update_kw:
            raise ValueError("Empty update set provided")

        async with self.session_maker() as session:
            await session.execute(
                update(Post).where(Post.id_ == post_id).values(**update_kw)
            )
            await session.commit()

    async def get_forum_posts(
        self,
        forum_id: int,
        limit: int,
        cursor: int = 0,
        sort_option: SortOption = SortOption.DESCENDING,
        datetime_bound: datetime | None = None,
    ) -> list[PostResult]:
        datetime_bound = datetime_bound or datetime.min
        match sort_option:
            case SortOption.DESCENDING:
                order_clause = Post.time_posted.desc
            case SortOption.ASCENDING:
                order_clause = Post.time_posted.asc

        async with self.session_maker() as session:
            posts: list[Row[tuple[Post, str]]] = list(
                (
                    await session.execute(
                        select(Post, User.username)
                        .join(User, Post.author_id == User.id_)
                        .where(
                            (Post.id_ > cursor)
                            & (Post.forum_id == forum_id)
                            & (Post.time_posted > datetime_bound)
                        )
                        .order_by(order_clause)
                        .limit(limit)
                    )
                ).all()
            )

            return [PostResult.construct_from_orm(*p.tuple()) for p in posts]

    async def get_user_posts(
        self,
        user_id: int,
        limit: int,
        cursor: int = 0,
        sort_option: SortOption = SortOption.DESCENDING,
    ) -> list[PostResult]:
        match sort_option:
            case SortOption.DESCENDING:
                order_clause = Post.time_posted.desc
            case SortOption.ASCENDING:
                order_clause = Post.time_posted.asc

        async with self.session_maker() as session:
            posts: list[Row[tuple[Post, str]]] = list(
                (
                    await session.execute(
                        select(Post, User.username)
                        .join(User, Post.author_id == User.id_)
                        .where((Post.id_ > cursor) & (Post.author_id == user_id))
                        .order_by(order_clause)
                        .limit(limit)
                    )
                ).all()
            )

            return [PostResult.construct_from_orm(*p.tuple()) for p in posts]

    async def check_saved(self, post_id: int, user_id) -> bool:
        async with self.session_maker() as session:
            saved: PostSave | None = (
                await session.execute(
                    select(PostSave).where(
                        (PostSave.user_id == user_id) & (PostSave.post_id == post_id)
                    )
                )
            ).scalar_one_or_none()
            return bool(saved)

    async def get_vote(self, post_id: int, user_id: int) -> bool | None:
        async with self.session_maker() as session:
            vote: PostVote | None = (
                await session.execute(
                    select(PostVote).where(
                        (PostVote.voter_id == user_id) & (PostVote.post_id == post_id)
                    )
                )
            ).scalar_one_or_none()

            if not vote:
                return None

            return vote.vote_type

    async def check_reported(
        self, post_id: int, user_id, report_tag: ReportTags
    ) -> bool:
        async with self.session_maker() as session:
            report: PostReport | None = (
                await session.execute(
                    select(PostReport).where(
                        (PostReport.user_id == user_id)
                        & (PostReport.post_id == post_id)
                        & (PostReport.report_tag == report_tag.value)
                    )
                )
            ).scalar_one_or_none()
            return bool(report)
