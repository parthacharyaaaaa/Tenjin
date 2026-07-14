from enum import StrEnum
from types import MappingProxyType
from typing import Final


class SideEffectType(StrEnum):
    __NAME__ = "SIDE_EFFECTS_TYPES"

    CACHE_INVALIDATION = "CACHE_INVALIDATION"
    INTENT_INVALIDATION = "INTENT_INVALIDATION"
    COUNTER_UPDATE = "COUNTER_UPDATE"


class EventLiteral(StrEnum):
    """Table and column names for event table"""

    EVENTS_TABLE_NAME = "stream_events"
    EVENT_ID_COLUMN_NAME = "event_id"
    EVENT_TIMESTAMP_COLUMN_NAME = "acknowledgement_time"


class DeadLetterQueueLiteral(StrEnum):
    """Table and column names for DLQ purposes"""

    TABLE_NAME = "dlq_events"
    PAYLOAD_COLUMN_NAME = "payload"

    COUNTERS_TABLE_NAME = "counters_dlq"
    COUNTERS_FAILURE_TIME_COLUMN_NAME = "failure_time"
    COUNTERS_AFFECTED_RELATION_COLUMN_NAME = "affected_table"
    COUNTERS_AFFECTED_COLUMN_COLUMN_NAME = "affected_column"

    FAILED_SIDE_EFFECTS_TABLE_NAME = "side_effects_dlq"
    SIDE_EFFECT_TYPE_COLUMN_NAME = "side_effect_type"


class EventMetadataLiteral(StrEnum):
    """Names for event-tracking columns assosciated with database relations"""

    LAST_EVENT_IDENTIFIER_COLUMN_NAME = "last_event_id"
    EVENT_SAVE_COLUMN_NAME = "is_saved"
    EVENT_SUB_COLUMN_NAME = "is_subscribed"
    EVENT_VOTE_COLUMN_NAME = "vote_type"


class DeletionColumnLiteral(StrEnum):
    """Names for deletion-related columns"""

    DELETED_COLUMN_NAME = "deleted"
    DELETION_TIME_COLUMN_NAME = "deletion_time"
    DELETION_AUTHOR_EVENT = "deletion_author_event"


class ForeignKeyColumnLiteral(StrEnum):
    """Names for foreign keys columns"""

    AUTHOR_ID = "author_id"
    PARENT_FORUM = "parent_forum"
    PARENT_POST = "parent_post"
    PARENT_ANIME = "parent_anime"


class AssociationColumnLiteral(StrEnum):
    """Names for foreign key columns in association tables"""

    POST_ID = "post_id"
    USER_ID = "user_id"
    COMMENT_ID = "comment_id"
    FORUM_ID = "forum_id"
    ANIME_ID = "anime_id"
    GENRE_ID = "genre_id"


class StrongEntity(StrEnum):
    """Column names for strong entities"""

    GENRE = "genres"
    ANIME = "animes"
    USER = "users"
    FORUM = "forums"
    POST = "posts"
    COMMENT = "comments"


class GenericLiterals(StrEnum):
    """Generic, common column names"""

    ID = "id_"


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
