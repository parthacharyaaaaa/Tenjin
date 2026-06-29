from typing import Annotated

from pydantic import BaseModel, Field
from pydantic_settings import SettingsConfigDict


class BaseWorkerModel(BaseModel):
    model_config = SettingsConfigDict(
        toml_file="config.toml",
        extra="forbid",
    )

    @property
    def dormant(self) -> bool:
        return any(self.model_dump().values())


class UserWorkerSettings(BaseWorkerModel):
    USER_CLEANUP: Annotated[int, Field(ge=0, default=0)]


class PostWorkerSettings(BaseWorkerModel):
    POST_CREATE: Annotated[int, Field(ge=0, default=0)]
    POST_SAVE: Annotated[int, Field(ge=0, default=0)]
    POST_UNSAVE: Annotated[int, Field(ge=0, default=0)]
    POST_REPORT: Annotated[int, Field(ge=0, default=0)]
    POST_VOTE: Annotated[int, Field(ge=0, default=0)]
    POST_UNVOTE: Annotated[int, Field(ge=0, default=0)]
    POST_DELETE: Annotated[int, Field(ge=0, default=0)]


class CommentWorkerSettings(BaseWorkerModel):
    COMMENT_CREATE: Annotated[int, Field(ge=0, default=0)]
    COMMENT_VOTE: Annotated[int, Field(ge=0, default=0)]
    COMMENT_UNVOTE: Annotated[int, Field(ge=0, default=0)]
    COMMENT_REPORT: Annotated[int, Field(ge=0, default=0)]
    COMMENT_DELETE: Annotated[int, Field(ge=0, default=0)]


class AnimeWorkerSettings(BaseWorkerModel):
    ANIME_SUB: Annotated[int, Field(ge=0, default=0)]
    ANIME_UNSUB: Annotated[int, Field(ge=0, default=0)]


class ForumWorkerSettings(BaseWorkerModel):
    FORUM_SUB: Annotated[int, Field(ge=0, default=0)]
    FORUM_UNSUB: Annotated[int, Field(ge=0, default=0)]


class CounterWorkerSettings(BaseWorkerModel):
    COUNTERS: Annotated[int, Field(ge=0, default=0)]


class OrphanedWorkerSettings(BaseWorkerModel):
    ORPHANED_POST_DELETE: Annotated[int, Field(ge=0, default=0)]
    ORPHANED_COMMENT_DELETE: Annotated[int, Field(ge=0, default=0)]


class DownstreamWorkerSettings(BaseWorkerModel):
    DOWNSTREAM_POST_DECREMENT: Annotated[int, Field(ge=0, default=0)]
    DOWNSTREAM_COMMENT_DECREMENT: Annotated[int, Field(ge=0, default=0)]


class DeadLetterWorkerSettings(BaseWorkerModel):
    STANDARD_DLQ: Annotated[int, Field(ge=0, default=1)]
    COUNTERS_DLQ: Annotated[int, Field(ge=0, default=0)]
