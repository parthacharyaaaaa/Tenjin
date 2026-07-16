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

    # Strong entity deletions
    forum_deletions: Queue[tuple[StreamedEvent]] = field(default_factory=Queue)
    post_deletions: Queue[tuple[StreamedEvent]] = field(default_factory=Queue)
    comment_deletions: Queue[tuple[StreamedEvent]] = field(default_factory=Queue)
    user_cleanup: Queue[tuple[StreamedEvent]] = field(default_factory=Queue)

    # Downstream orphan deletions
    downstream_posts: Queue[StreamedEvent] = field(default_factory=Queue)
    downstream_comments: Queue[StreamedEvent] = field(default_factory=Queue)

    # Downstream counter decrements
    downstream_user_posts_counters: Queue[StreamedEvent] = field(default_factory=Queue)
    downstream_forums_posts_counters: Queue[StreamedEvent] = field(
        default_factory=Queue
    )
    downstream_users_comments_counters: Queue[StreamedEvent] = field(
        default_factory=Queue
    )
    downstream_posts_comments_counters: Queue[StreamedEvent] = field(
        default_factory=Queue
    )

    # DLQ
    dead_letter: Queue[StreamedEvent] = field(default_factory=Queue)
    counter_dead_letter: Queue[DeadCounterBatch] = field(default_factory=Queue)
    side_effects_dead_letter: Queue[StreamedEvent] = field(default_factory=Queue)

    @cached_property
    def event_queue_mapping(
        self,
    ) -> MappingProxyType[EventName, Queue[tuple[StreamedEvent]]]:
        return MappingProxyType(
            {
                # Posts
                EventName.POST_CREATE: self.post_insertions,
                EventName.POST_SAVE: self.post_save_insertions,
                EventName.POST_UNSAVE: self.post_save_insertions,
                EventName.POST_REPORT: self.post_report_insertions,
                EventName.POST_VOTE: self.post_vote_insertions,
                EventName.POST_UNVOTE: self.post_vote_insertions,
                EventName.POST_DELETE: self.post_deletions,
                # Comments
                EventName.COMMENT_CREATE: self.comment_insertions,
                EventName.COMMENT_VOTE: self.comment_vote_insertions,
                EventName.COMMENT_UNVOTE: self.comment_vote_insertions,
                EventName.COMMENT_REPORT: self.comment_report_insertions,
                EventName.COMMENT_DELETE: self.comment_deletions,
                # Subscriptions
                EventName.FORUM_SUB: self.forum_subscription_insertions,
                EventName.FORUM_UNSUB: self.forum_subscription_insertions,
                EventName.ANIME_SUB: self.anime_subscription_insertions,
                EventName.ANIME_UNSUB: self.anime_subscription_insertions,
                # Cleanup
                EventName.USER_CLEANUP: self.user_cleanup,
            }
        )

    @cached_property
    def downstream_deletion_event_queue_apping(
        self,
    ) -> MappingProxyType[EventName, Queue[StreamedEvent]]:
        return MappingProxyType(
            {
                EventName.ORPHANED_COMMENT_DELETE: self.downstream_comments,
                EventName.ORPHANED_POST_DELETE: self.downstream_posts,
            }
        )

    @cached_property
    def downstream_decrement_event_queue_mapping(
        self,
    ) -> MappingProxyType[EventName, Queue[StreamedEvent]]:
        return MappingProxyType(
            {
                EventName.DOWNSTREAM_FORUM_POST_DECREMENT: self.downstream_forums_posts_counters,
                EventName.DOWNSTREAM_USER_POST_DECREMENT: self.downstream_user_posts_counters,
                EventName.DOWNSTREAM_POST_COMMENT_DECREMENT: self.downstream_posts_comments_counters,
                EventName.DOWNSTREAM_USER_COMMENT_DECREMENT: self.downstream_users_comments_counters,
            }
        )

    @cached_property
    def dead_letter_queue_mapping(self) -> MappingProxyType[str, Queue]:
        return MappingProxyType(
            {
                EventName.DLQ_STANDARD: self.dead_letter,
                EventName.DLQ_COUNTER: self.counter_dead_letter,
                EventName.DLQ_SIDE_EFFECTS: self.side_effects_dead_letter,
            }
        )
