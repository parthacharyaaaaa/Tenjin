"""
Event-specific worker dependency mappings
"""

from resource_database_workers.dependencies.annotations import (
    IDENTIFIER_COLUMN,
    ACTION_LITERAL,
    TABLE,
)
from typing import Any, Callable, Final
from types import MappingProxyType

from resource_auxillary.datastructures.database import GenericLiterals
from resource_auxillary.datastructures.database import StrongEntity
from resource_auxillary.strings import EventName

from resource_database_workers.tasks.consumer import (
    queue_insertion_consumer,
    queue_deletion_consumer,
    queue_downstream_decrement_consumer,
    queue_downstream_deletion_consumer,
)
from resource_database_workers.tasks.dlq_workers import dlq_consumer

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
            EventName.POST_CREATE: {ACTION_LITERAL: "None"},
            EventName.COMMENT_CREATE: {ACTION_LITERAL: "None"},
            EventName.POST_VOTE: {ACTION_LITERAL: "vote"},
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
                EventName.POST_CREATE: queue_insertion_consumer,
                EventName.COMMENT_CREATE: queue_insertion_consumer,
                EventName.POST_DELETE: queue_deletion_consumer,
                EventName.COMMENT_DELETE: queue_deletion_consumer,
                EventName.FORUM_DELETE: queue_deletion_consumer,
                EventName.POST_SAVE: queue_insertion_consumer,
                EventName.POST_UNSAVE: queue_insertion_consumer,
                EventName.POST_VOTE: queue_insertion_consumer,
                EventName.POST_UNVOTE: queue_insertion_consumer,
                EventName.COMMENT_VOTE: queue_insertion_consumer,
                EventName.COMMENT_UNVOTE: queue_insertion_consumer,
                EventName.FORUM_SUB: queue_insertion_consumer,
                EventName.FORUM_UNSUB: queue_insertion_consumer,
                EventName.ANIME_SUB: queue_insertion_consumer,
                EventName.ANIME_UNSUB: queue_insertion_consumer,
                EventName.ORPHANED_POST_DELETE: queue_downstream_deletion_consumer,
                EventName.ORPHANED_COMMENT_DELETE: queue_downstream_deletion_consumer,
                EventName.DOWNSTREAM_USER_POST_DECREMENT: queue_downstream_decrement_consumer,
                EventName.DOWNSTREAM_USER_COMMENT_DECREMENT: queue_downstream_decrement_consumer,
                EventName.DOWNSTREAM_FORUM_POST_DECREMENT: queue_downstream_decrement_consumer,
                EventName.DOWNSTREAM_POST_COMMENT_DECREMENT: queue_downstream_decrement_consumer,
                EventName.DLQ_COUNTER: dlq_consumer,
                EventName.DLQ_SIDE_EFFECTS: dlq_consumer,
            }.items()
        }
    )
)
