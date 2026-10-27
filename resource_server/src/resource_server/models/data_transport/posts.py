from collections.abc import Mapping
from datetime import datetime
from typing import Any, ClassVar, Self

from resource_server.models.database import Post
from resource_server.repositories.result_protocol import AbstractDTO


class PostResult(AbstractDTO):
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
    body_text: str | None

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
    def construct_from_orm(cls, obj: Post, /, author_username: str) -> Self:
        return cls(
            id_=obj.id_,
            author_id=obj.author_id,
            forum_id=obj.forum_id,
            author_username=author_username,
            score=obj.score,
            total_comments=obj.total_comments,
            saves=obj.saves,
            reports=obj.reports,
            title=obj.title,
            body_text=obj.body_text,
            flair=obj.flair,
            closed=obj.closed,
            time_posted=obj.time_posted,
        )
