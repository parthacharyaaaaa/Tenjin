"""
Event-specific worker dependency mappings
"""

from types import MappingProxyType
from typing import Any, Callable, Final

from resource_auxillary.datastructures.database import GenericLiterals, StrongEntity
from resource_auxillary.strings import EventName

from resource_database_workers.dependencies.annotations import (
    ACTION_LITERAL,
    IDENTIFIER_COLUMN,
    TABLE,
)
from resource_database_workers.tasks.consumer import (
    queue_deletion_consumer,
    queue_insertion_consumer,
    user_orphan_consumer,
)
from resource_database_workers.tasks.dlq_workers import dlq_consumer
from resource_database_workers.tasks.side_effects import (
    downstream_deletion_worker,
)

type t_event_worker_data = tuple[Callable[..., Any], dict[Any, Any]]
_EMPTY_DICT_SENTINEL: Final[dict[Any, Any]] = {}

_DELETION_EVENT_ARGS_MAPPING: Final[MappingProxyType[EventName, dict[Any, Any]]] = (
    MappingProxyType(
        {
            EventName.POST_DELETE: {
                TABLE: StrongEntity.POST,
                IDENTIFIER_COLUMN: GenericLiterals.ID.value,
            },
            EventName.COMMENT_DELETE: {
                TABLE: StrongEntity.COMMENT,
                IDENTIFIER_COLUMN: GenericLiterals.ID.value,
            },
            EventName.FORUM_DELETE: {
                TABLE: StrongEntity.FORUM,
                IDENTIFIER_COLUMN: GenericLiterals.ID.value,
            },
        }
    )
)

_INSERTION_EVENT_ARGS_MAPPING: Final[MappingProxyType[EventName, dict[Any, Any]]] = (
    MappingProxyType(
        {
            EventName.POST_CREATE: {ACTION_LITERAL: None},
            EventName.COMMENT_CREATE: {ACTION_LITERAL: None},
            EventName.USER_TICKET: {ACTION_LITERAL: None},
            EventName.POST_VOTE: {ACTION_LITERAL: "vote"},
            EventName.POST_REPORT: {ACTION_LITERAL: None},
            EventName.POST_UNVOTE: {ACTION_LITERAL: "vote"},
            EventName.POST_SAVE: {ACTION_LITERAL: "save"},
            EventName.POST_UNSAVE: {ACTION_LITERAL: "save"},
            EventName.ANIME_SUB: {ACTION_LITERAL: "subscribe"},
            EventName.ANIME_UNSUB: {ACTION_LITERAL: "subscribe"},
            EventName.FORUM_SUB: {ACTION_LITERAL: "subscribe"},
            EventName.FORUM_UNSUB: {ACTION_LITERAL: "subscribe"},
        }
    )
)

EVENT_WORKER_DATA_MAPPING: Final[MappingProxyType[EventName, t_event_worker_data]] = (
    MappingProxyType(
        {
            event: (
                worker,
                _INSERTION_EVENT_ARGS_MAPPING.get(event)
                or _DELETION_EVENT_ARGS_MAPPING.get(event)
                or _EMPTY_DICT_SENTINEL,
            )
            for event, worker in {
                EventName.USER_CLEANUP: user_orphan_consumer,
                EventName.USER_TICKET: queue_insertion_consumer,
                EventName.POST_CREATE: queue_insertion_consumer,
                EventName.POST_DELETE: queue_deletion_consumer,
                EventName.POST_REPORT: queue_insertion_consumer,
                EventName.POST_SAVE: queue_insertion_consumer,
                EventName.POST_UNSAVE: queue_insertion_consumer,
                EventName.POST_VOTE: queue_insertion_consumer,
                EventName.POST_UNVOTE: queue_insertion_consumer,
                EventName.COMMENT_CREATE: queue_insertion_consumer,
                EventName.COMMENT_DELETE: queue_deletion_consumer,
                EventName.COMMENT_VOTE: queue_insertion_consumer,
                EventName.COMMENT_UNVOTE: queue_insertion_consumer,
                EventName.COMMENT_REPORT: queue_insertion_consumer,
                EventName.FORUM_DELETE: queue_deletion_consumer,
                EventName.FORUM_SUB: queue_insertion_consumer,
                EventName.FORUM_UNSUB: queue_insertion_consumer,
                EventName.ANIME_SUB: queue_insertion_consumer,
                EventName.ANIME_UNSUB: queue_insertion_consumer,
                EventName.ORPHANED_POST_DELETE: downstream_deletion_worker,
                EventName.ORPHANED_COMMENT_DELETE: downstream_deletion_worker,
                EventName.DLQ_COUNTER: dlq_consumer,
                EventName.DLQ_SIDE_EFFECTS: dlq_consumer,
                EventName.DEAD_LETTER_SENTINEL: dlq_consumer,
            }.items()
        }
    )
)
