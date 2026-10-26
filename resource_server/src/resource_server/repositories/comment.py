from dataclasses import dataclass

from auxillary.singleton import SingletonMetaclass
from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from resource_server.models.data_transport.comment import CommentResult
from resource_server.models.database import Comment, CommentReport, CommentVote, User
from resource_server.models.database_enums import ReportTags

type t_comment_result = Row[tuple[Comment, int, str]]


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

    async def get_vote(self, comment_id: int, user_id: int) -> bool | None:
        async with self.session_maker() as session:
            vote: CommentVote | None = (
                await session.execute(
                    select(CommentVote).where(
                        (CommentVote.voter_id == user_id)
                        & (CommentVote.comment_id == comment_id)
                    )
                )
            ).scalar_one_or_none()

            if not vote:
                return None

            return vote.vote_type

    async def check_reported(
        self, comment_id: int, user_id, report_tag: ReportTags
    ) -> bool:
        async with self.session_maker() as session:
            report: CommentReport | None = (
                await session.execute(
                    select(CommentReport).where(
                        (CommentReport.user_id == user_id)
                        & (CommentReport.comment_id == comment_id)
                        & (CommentReport.report_tag == report_tag.value)
                    )
                )
            ).scalar_one_or_none()
            return bool(report)
