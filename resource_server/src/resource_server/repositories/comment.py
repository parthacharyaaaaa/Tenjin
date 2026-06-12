from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Self

from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from resource_server.repositories.result_protocol import AbstractResult
from resource_server.models.database import Comment, User
from resource_server.utils.singleton import SingletonMetaclass

type t_comment_result = Row[tuple[Comment, int, str]]


@dataclass(slots=True, init=False)
class CommentResult(AbstractResult):
    id_: int
    author_id: int
    author_username: str
    parent_forum: int  # XXX: Unneeded much?
    parent_post: int

    time_created: datetime
    body: str

    score: int
    reports: int

    resource_name: ClassVar[str] = Comment.__tablename__
    COUNTER_FIELDS: ClassVar[tuple[str, ...]] = (
        "score",
        "reports",
    )

    @classmethod
    def construct_from_orm(
        cls, obj: Comment, author_id: int, author_username: str, *args, **kwargs
    ) -> Self:
        instance = super().construct_from_orm(obj, *args, **kwargs)
        instance.author_id = author_id
        instance.author_username = author_username

        return instance


@dataclass(slots=True, weakref_slot=True)
class CommentRepository(metaclass=SingletonMetaclass):
    session_maker: async_sessionmaker[AsyncSession]

    async def get_comment(self, comment_id: int) -> CommentResult | None:
        async with self.session_maker() as session:
            result: t_comment_result | None = (
                await session.execute(
                    select(Comment, User.id_, User.username)
                    .select_from(Comment)
                    .join(User, User.id_ == Comment.author_id)
                    .where(Comment.id_ == comment_id)
                )
            ).first()

            if not result:
                return None

            return CommentResult.construct_from_orm(*result.tuple())

    async def get_post_comments(
        self, post_id: int, limit: int, cursor: int = 0
    ) -> list[CommentResult]:
        async with self.session_maker() as session:
            results: list[t_comment_result] = list(
                (
                    await session.execute(
                        select(Comment, User.id_, User.username)
                        .select_from(Comment)
                        .join(User, User.id_ == Comment.author_id)
                        .where(
                            (Comment.id_ > cursor) & (Comment.parent_post == post_id)
                        )
                    )
                ).all()
            )

            return [CommentResult.construct_from_orm(*r.tuple()) for r in results]
