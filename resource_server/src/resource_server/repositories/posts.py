from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any, ClassVar, Mapping, Self

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from resource_server.datastructures.requests import SortOption
from resource_server.utils.singleton import SingletonMetaclass
from resource_server.repositories.result_protocol import AbstractResult
from resource_server.models.database import Post

from resource_auxillary.strings import NAME_SEPERATOR


@dataclass(slots=True, init=False)
class PostResult(AbstractResult):
    id_: int
    author_id: int
    forum_id: int

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

    @lru_cache(maxsize=1)
    @classmethod
    def get_counter_fields(cls) -> dict[str, str]:
        return {
            i: NAME_SEPERATOR.join((Post.__tablename__, i)) for i in cls.COUNTER_FIELDS
        }

    @classmethod
    def construct_from_cache(cls, mapping: Mapping[str, Any]) -> Self:
        instance = cls()

        instance.id_ = mapping["id"]
        instance.author_id = mapping["author_id"]
        instance.forum_id = mapping["forum_id"]

        instance.score = mapping["score"]
        instance.total_comments = mapping["total_comments"]
        instance.saves = mapping["saves"]
        instance.reports = mapping["reports"]

        instance.title = mapping["title"]
        instance.body_text = mapping["body_text"]

        instance.flair = mapping["flair"]
        instance.closed = mapping["closed"]
        instance.time_posted = mapping["time_posted"]

        return instance

    @classmethod
    def construct_from_orm(
        cls,
        obj: Post,
        *args,
        **kwargs,
    ) -> Self:
        instance = cls()

        instance.id_ = obj.id_
        instance.author_id = obj.author_id
        instance.forum_id = obj.forum_id

        instance.score = obj.score
        instance.total_comments = obj.total_comments
        instance.saves = obj.saves
        instance.reports = obj.reports

        instance.title = obj.title
        instance.body_text = obj.body_text

        instance.flair = obj.flair
        instance.closed = obj.closed
        instance.time_posted = obj.time_posted

        return instance


@dataclass(slots=True, weakref_slot=True)
class PostRepository(metaclass=SingletonMetaclass):
    session_maker: async_sessionmaker[AsyncSession]

    async def get_post(self, post_id) -> PostResult | None:
        async with self.session_maker() as session:
            post: Post | None = (
                await session.execute(select(Post).where(Post.id_ == post_id))
            ).scalar_one_or_none()
            if not post:
                return None

            return PostResult.construct_from_orm(post)

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
            posts: list[Post] = list(
                (
                    await session.execute(
                        select(Post)
                        .where(
                            (Post.id_ > cursor)
                            & (Post.forum_id == forum_id)
                            & (Post.time_posted > datetime)
                        )
                        .order_by(order_clause)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )

            return [PostResult.construct_from_orm(p) for p in posts]
