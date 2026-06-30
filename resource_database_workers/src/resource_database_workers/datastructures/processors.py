from typing import Final
from types import MappingProxyType

from resource_auxillary.strings import EventName

from resource_database_workers.tasks.consumer import (
    queue_insertion_consumer,
    queue_deletion_consumer,
    queue_downstream_decrement_consumer,
    queue_downstream_deletion_consumer,
)

EVENT_WORKER_MAPPING: Final[MappingProxyType] = MappingProxyType(
    {
        # Strong entity deletions
        EventName.POST_DELETE: queue_insertion_consumer,
        EventName.COMMENT_DELETE: queue_insertion_consumer,
        EventName.FORUM_DELETE: queue_insertion_consumer,
        # Association entity upserts
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
        # Downstream orphan deletions
        EventName.ORPHANED_POST_DELETE: queue_downstream_deletion_consumer,
        EventName.ORPHANED_COMMENT_DELETE: queue_downstream_deletion_consumer,
        # Downstream counters
        EventName.DOWNSTREAM_USER_POST_DECREMENT: queue_downstream_decrement_consumer,
        EventName.DOWNSTREAM_USER_COMMENT_DECREMENT: queue_downstream_decrement_consumer,
        EventName.DOWNSTREAM_FORUM_POST_DECREMENT: queue_downstream_decrement_consumer,
        EventName.DOWNSTREAM_POST_COMMENT_DECREMENT: queue_downstream_decrement_consumer,
    }
)
