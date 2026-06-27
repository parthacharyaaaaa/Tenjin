from asyncio import Queue
from dataclasses import dataclass, field
from functools import cached_property
from types import MappingProxyType

from auxillary.singleton import SingletonMetaclass

from resource_auxillary.events import StreamedEvent
from resource_auxillary.strings import EventName

from resource_database_workers.datastructures.dead_counter_batch import DeadCounterBatch


@dataclass(slots=True, frozen=True)
class QueueRegistry(metaclass=SingletonMetaclass):
    # Strong entity insertions
    post_insertions: Queue[tuple[StreamedEvent]] = field(default_factory=Queue)
    comment_insertions: Queue[tuple[StreamedEvent]] = field(default_factory=Queue)

    # Weak entity insertions
    post_report_insertions: Queue[tuple[StreamedEvent]] = field(default_factory=Queue)
    post_save_insertions: Queue[tuple[StreamedEvent]] = field(default_factory=Queue)
    post_vote_insertions: Queue[tuple[StreamedEvent]] = field(default_factory=Queue)
    comment_report_insertions: Queue[tuple[StreamedEvent]] = field(
        default_factory=Queue
    )
    comment_vote_insertions: Queue[tuple[StreamedEvent]] = field(default_factory=Queue)
    forum_subscription_insertions: Queue[tuple[StreamedEvent]] = field(
        default_factory=Queue
    )
    anime_subscription_insertions: Queue[tuple[StreamedEvent]] = field(
        default_factory=Queue
    )

    # Weak entity deletions
    post_save_deletions: Queue[tuple[StreamedEvent]] = field(default_factory=Queue)
    post_vote_deletions: Queue[tuple[StreamedEvent]] = field(default_factory=Queue)
    comment_vote_deletions: Queue[tuple[StreamedEvent]] = field(default_factory=Queue)
    forum_subscription_deletions: Queue[tuple[StreamedEvent]] = field(
        default_factory=Queue
    )
    anime_subscription_deletions: Queue[tuple[StreamedEvent]] = field(
        default_factory=Queue
    )

    # Strong entity deletions
    post_deletions: Queue[tuple[StreamedEvent]] = field(default_factory=Queue)
    comment_deletions: Queue[tuple[StreamedEvent]] = field(default_factory=Queue)

    # Deletions
    user_deletions: Queue[tuple[StreamedEvent]] = field(default_factory=Queue)
    forum_deletions: Queue[tuple[StreamedEvent]] = field(default_factory=Queue)
    user_recovery: Queue[tuple[StreamedEvent]] = field(default_factory=Queue)

    # DLQ
    dead_letter: Queue[StreamedEvent] = field(default_factory=Queue)
    counter_dead_letter: Queue[DeadCounterBatch] = field(default_factory=Queue)

    @cached_property
    def event_queue_mapping(
        self,
    ) -> MappingProxyType[EventName, Queue[tuple[StreamedEvent]]]:
        return MappingProxyType(
            {
                EventName.POST_CREATE: self.post_insertions,
                EventName.POST_SAVE: self.post_save_insertions,
                EventName.POST_UNSAVE: self.post_save_deletions,
                EventName.POST_REPORT: self.post_report_insertions,
                EventName.POST_VOTE: self.post_vote_insertions,
                EventName.POST_UNVOTE: self.post_vote_deletions,
                EventName.POST_DELETE: self.post_deletions,
                EventName.COMMENT_CREATE: self.comment_insertions,
                EventName.COMMENT_VOTE: self.comment_vote_insertions,
                EventName.COMMENT_UNVOTE: self.comment_vote_deletions,
                EventName.COMMENT_REPORT: self.comment_report_insertions,
                EventName.COMMENT_DELETE: self.comment_deletions,
                EventName.FORUM_SUB: self.forum_subscription_insertions,
                EventName.FORUM_UNSUB: self.forum_subscription_deletions,
                EventName.ANIME_SUB: self.anime_subscription_insertions,
                EventName.ANIME_UNSUB: self.anime_subscription_deletions,
            }
        )
