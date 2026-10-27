from datetime import datetime
from typing import ClassVar, Self

from resource_server.models.database import Comment
from resource_server.repositories.result_protocol import AbstractDTO


class CommentResult(AbstractDTO):
    id_: int
    author_id: int
    author_username: str
    parent_forum: int
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
    def construct_from_orm(cls, obj: Comment, /, author_username: str) -> Self:
        return cls(
            id_=obj.id_,
            author_id=obj.author_id,
            author_username=author_username,
            parent_forum=obj.parent_forum,
            parent_post=obj.parent_post,
            time_created=obj.time_created,
            body=obj.body,
            score=obj.score,
            reports=obj.reports,
        )
