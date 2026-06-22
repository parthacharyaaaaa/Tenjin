from enum import StrEnum
from types import MappingProxyType
from typing import Final, LiteralString

EVENTS_TABLE_NAME: Final[LiteralString] = "stream_events"
EVENT_ID_COLUMN_NAME: Final[LiteralString] = "event_id"
EVENT_TIMESTAMP_COLUMN_NAME: Final[LiteralString] = "acknowledgement_time"

DLQ_TABLE_NAME: Final[LiteralString] = "dlq_events"
DLQ_PAYLOAD_COLUMN_NAME: Final[LiteralString] = "payload"

COUNTERS_DLQ_TABLE_NAME: Final[LiteralString] = "counters_dlq"
COUNTERS_DLQ_FAILURE_TIME_COLUMN_NAME: Final[LiteralString] = "failure_time"
COUNTERS_DLQ_AFFECTED_RELATION_COLUMN_NAME: Final[LiteralString] = "affected_table"
COUNTERS_DLQ_AFFECTED_COLUMN_COLUMN_NAME: Final[LiteralString] = "affected_column"

LAST_EVENT_IDENTIFIER_COLUMN_NAME: Final[LiteralString] = "last_event_id"
EVENT_SAVE_COLUMN_NAME: Final[LiteralString] = "is_saved"
EVENT_SUB_COLUMN_NAME: Final[LiteralString] = "is_subscribed"
EVENT_VOTE_COLUMN_NAME: Final[LiteralString] = "vote_type"

DELETED_COLUMN_NAME: Final[LiteralString] = "deleted"
DELETED_AT_COLUMN_NAME: Final[LiteralString] = "deleted_at"


class StrongEntity(StrEnum):
    USER = "users"
    POST = "posts"
    COMMENT = "comments"
    FORUM = "forums"


ENTITY_PK_MAPPING: Final[MappingProxyType[StrongEntity, str]] = MappingProxyType(
    {
        StrongEntity.POST: "post_id",
        StrongEntity.COMMENT: "comment_id",
        StrongEntity.FORUM: "forum_id",
    }
)

ORPHAN_MAPPING: Final[MappingProxyType[StrongEntity, tuple[str, str, str]]] = (
    MappingProxyType(
        {
            StrongEntity.FORUM: ("id_", StrongEntity.POST.value, "forum_id"),
            StrongEntity.POST: ("id_", StrongEntity.COMMENT.value, "parent_post"),
        }
    )
)
