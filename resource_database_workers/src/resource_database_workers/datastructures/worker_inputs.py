import asyncio
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final, Mapping
from uuid import uuid4

from psycopg_pool import AsyncConnectionPool

from redis.asyncio import Redis

from resource_auxillary.datastructures.database import StrongEntity
from resource_auxillary.events import StreamedEvent
from resource_auxillary.datastructures.database import GenericLiterals
from resource_auxillary.strings import EventName, StreamName
from resource_database_workers.config.config import AppConfig

from resource_database_workers.dependencies import (
    get_connection_pool,
    get_config,
    get_app_redis,
    get_internal_redis,
    get_queue_registry,
)
from resource_database_workers.datastructures.queues import QueueRegistry
from resource_database_workers.tasks.deletions import (
    downstream_soft_delete_strong_entity,
)
from resource_database_workers.tasks.insertions import (
    batch_association_insert_with_isolation,
)
from resource_database_workers.utils.typing import (
    BatchDownstreamDeletionFunction,
    BatchInsertionFunction,
    t_action_literal,
)

QUEUE_REGISTRY: Final[QueueRegistry] = get_queue_registry()


@dataclass(slots=True, kw_only=True)
class BaseInput:
    stream_name: StreamName
    pool: AsyncConnectionPool = field(default_factory=get_connection_pool)
    group_name: str = field(default=get_config().WORKER.CONSUMER_GROUP_NAME)
    dead_letter_queue: asyncio.Queue[StreamedEvent] = field(
        default=QUEUE_REGISTRY.dead_letter
    )


@dataclass(slots=True, kw_only=True)
class UpstreamDeletionInput(BaseInput):
    table: StrongEntity
    queue: asyncio.Queue[tuple[StreamedEvent]]
    identifier_column: str = field(default=GenericLiterals.ID.value)
    config: AppConfig = field(default_factory=get_config)
    redis: Redis = field(default_factory=get_app_redis)


@dataclass(slots=True, kw_only=True)
class InsertionInput(BaseInput):
    queue: asyncio.Queue[tuple[StreamedEvent]]
    action: t_action_literal | None
    config: AppConfig = field(default_factory=get_config)
    redis: Redis = field(default_factory=get_app_redis)
    batch_function: BatchInsertionFunction = field(
        default=batch_association_insert_with_isolation
    )


@dataclass(slots=True, kw_only=True)
class DownstreamDeletionInput(BaseInput):
    queue: asyncio.Queue[StreamedEvent]
    batch_function: BatchDownstreamDeletionFunction = field(
        default=downstream_soft_delete_strong_entity
    )
    config: AppConfig = field(default_factory=get_config)
    redis: Redis = field(default_factory=get_internal_redis)


@dataclass(slots=True, kw_only=True)
class DownstreamCounterDecrementInput(BaseInput):
    config: AppConfig = field(default_factory=get_config)
    redis: Redis = field(default_factory=get_internal_redis)
    queue: asyncio.Queue[StreamedEvent]


@dataclass(slots=True, kw_only=True)
class UpstreamDispatcherInput(BaseInput):
    config: AppConfig = field(default_factory=get_config)
    redis: Redis = field(default_factory=get_internal_redis)
    queue_mapping: Mapping[
        EventName, asyncio.Queue[tuple[StreamedEvent]] | asyncio.Queue[StreamedEvent]
    ]
    read_history: bool = field(default=True)
    consumer_name: str = field(default_factory=lambda: uuid4().hex)


@dataclass(slots=True, kw_only=True)
class DownstreamDispatcherInput(BaseInput):
    config: AppConfig = field(default_factory=get_config)
    redis: Redis = field(default_factory=get_internal_redis)
    queue_mapping: Mapping[EventName, asyncio.Queue[StreamedEvent]]
    read_history: bool = field(default=True)
    consumer_name: str = field(default_factory=lambda: uuid4().hex)


UPSTREAM_POST_DELETION_INPUT: Final[UpstreamDeletionInput] = UpstreamDeletionInput(
    table=StrongEntity.POST,
    stream_name=StreamName.POSTS,
    queue=QUEUE_REGISTRY.post_deletions,
)

UPSTREAM_COMMENT_DELETION_INPUT: Final[UpstreamDeletionInput] = UpstreamDeletionInput(
    table=StrongEntity.COMMENT,
    queue=QUEUE_REGISTRY.comment_deletions,
    stream_name=StreamName.COMMENTS,
)

UPSTREAM_FORUM_DELETION_INPUT: Final[UpstreamDeletionInput] = UpstreamDeletionInput(
    table=StrongEntity.FORUM,
    queue=QUEUE_REGISTRY.forum_deletions,
    stream_name=StreamName.FORUMS,
)


POST_CREATION_INPUT: Final[InsertionInput] = InsertionInput(
    stream_name=StreamName.POSTS, queue=QUEUE_REGISTRY.post_insertions, action=None
)

COMMENT_CREATION_INPUT: Final[InsertionInput] = InsertionInput(
    stream_name=StreamName.COMMENTS,
    queue=QUEUE_REGISTRY.comment_insertions,
    action=None,
)

POST_SAVE_INPUT: Final[InsertionInput] = InsertionInput(
    stream_name=StreamName.POSTS,
    queue=QUEUE_REGISTRY.post_save_insertions,
    action="save",
)

POST_VOTE_INPUT: Final[InsertionInput] = InsertionInput(
    stream_name=StreamName.POSTS,
    queue=QUEUE_REGISTRY.post_vote_insertions,
    action="vote",
)

COMMENT_VOTE_INPUT: Final[InsertionInput] = InsertionInput(
    stream_name=StreamName.COMMENTS,
    queue=QUEUE_REGISTRY.comment_vote_insertions,
    action="vote",
)

FORUM_SUB_INPUT: Final[InsertionInput] = InsertionInput(
    stream_name=StreamName.FORUMS,
    queue=QUEUE_REGISTRY.forum_subscription_insertions,
    action="subscribe",
)

ANIME_SUB_INPUT: Final[InsertionInput] = InsertionInput(
    stream_name=StreamName.ANIMES,
    queue=QUEUE_REGISTRY.anime_subscription_insertions,
    action="subscribe",
)


ORPHANED_POST_DELETE_INPUT: Final[DownstreamDeletionInput] = DownstreamDeletionInput(
    stream_name=StreamName.DOWNSTREAM_DELETIONS, queue=QUEUE_REGISTRY.downstream_posts
)

ORPHANED_COMMENT_DELETE_INPUT: Final[DownstreamDeletionInput] = DownstreamDeletionInput(
    stream_name=StreamName.DOWNSTREAM_DELETIONS,
    queue=QUEUE_REGISTRY.downstream_comments,
)


DOWNSTREAM_FORUMS_POSTS_COUNTER_DECREMENT: Final[DownstreamCounterDecrementInput] = (
    DownstreamCounterDecrementInput(
        queue=QUEUE_REGISTRY.downstream_forums_posts_counters,
        stream_name=StreamName.DOWNSTREAM_COUNTER_DECREMENTS,
    )
)

DOWNSTREAM_USERS_POSTS_COUNTER_DECREMENT: Final[DownstreamCounterDecrementInput] = (
    DownstreamCounterDecrementInput(
        queue=QUEUE_REGISTRY.downstream_user_posts_counters,
        stream_name=StreamName.DOWNSTREAM_COUNTER_DECREMENTS,
    )
)

DOWNSTREAM_POSTS_COMMENTS_COUNTER_DECREMENT: Final[DownstreamCounterDecrementInput] = (
    DownstreamCounterDecrementInput(
        queue=QUEUE_REGISTRY.downstream_posts_comments_counters,
        stream_name=StreamName.DOWNSTREAM_COUNTER_DECREMENTS,
    )
)

DOWNSTREAM_USERS_COMMENTS_COUNTER_DECREMENT: Final[DownstreamCounterDecrementInput] = (
    DownstreamCounterDecrementInput(
        queue=QUEUE_REGISTRY.downstream_users_comments_counters,
        stream_name=StreamName.DOWNSTREAM_COUNTER_DECREMENTS,
    )
)

WORKER_INPUT_DATA_MAPPING: Final[MappingProxyType[EventName, Any]] = MappingProxyType(
    {
        EventName.POST_CREATE: POST_CREATION_INPUT,
        EventName.COMMENT_CREATE: COMMENT_CREATION_INPUT,
        # Strong entity deletions
        EventName.POST_DELETE: UPSTREAM_COMMENT_DELETION_INPUT,
        EventName.COMMENT_DELETE: UPSTREAM_COMMENT_DELETION_INPUT,
        EventName.FORUM_DELETE: UPSTREAM_FORUM_DELETION_INPUT,
        # Association entity upserts
        EventName.POST_SAVE: POST_SAVE_INPUT,
        EventName.POST_UNSAVE: POST_SAVE_INPUT,
        EventName.POST_VOTE: POST_VOTE_INPUT,
        EventName.POST_UNVOTE: POST_VOTE_INPUT,
        EventName.COMMENT_VOTE: COMMENT_VOTE_INPUT,
        EventName.COMMENT_UNVOTE: COMMENT_VOTE_INPUT,
        EventName.FORUM_SUB: FORUM_SUB_INPUT,
        EventName.FORUM_UNSUB: FORUM_SUB_INPUT,
        EventName.ANIME_SUB: ANIME_SUB_INPUT,
        EventName.ANIME_UNSUB: ANIME_SUB_INPUT,
        # Downstream orphan deletions
        EventName.ORPHANED_POST_DELETE: ORPHANED_POST_DELETE_INPUT,
        EventName.ORPHANED_COMMENT_DELETE: ORPHANED_COMMENT_DELETE_INPUT,
        # Downstream counters
        EventName.DOWNSTREAM_USER_POST_DECREMENT: DOWNSTREAM_USERS_POSTS_COUNTER_DECREMENT,
        EventName.DOWNSTREAM_USER_COMMENT_DECREMENT: DOWNSTREAM_USERS_COMMENTS_COUNTER_DECREMENT,
        EventName.DOWNSTREAM_FORUM_POST_DECREMENT: DOWNSTREAM_FORUMS_POSTS_COUNTER_DECREMENT,
        EventName.DOWNSTREAM_POST_COMMENT_DECREMENT: DOWNSTREAM_POSTS_COMMENTS_COUNTER_DECREMENT,
    }
)
