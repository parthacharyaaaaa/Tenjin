from resource_auxillary.event_processing.event_stream_manager import EventStreamManager
from datetime import datetime
from typing import Iterable

from redis.asyncio import Redis

from resource_auxillary.cache import derive_cache_key
from resource_auxillary.datastructures.database import StrongEntity
from resource_auxillary.events import Event
from resource_auxillary.event_processing.qos import (
    execute_with_redis_retries,
)
from resource_auxillary.strings import EventName, StreamName

from resource_database_workers.datastructures.downstream import (
    DOWNSTREAM_DECREMENT_MAPPING,
    AnonymousDownstreamDeletionData,
    DownstreamCounterDecrementData,
    DownstreamDeletionData,
    DownstreamDeletionMapping,
    t_downstream_counter_event_metadata,
)

from resource_database_workers.config.sub_config import (
    WorkerConfig,
)


async def dispatch_downstream_events(
    event_stream_manager: EventStreamManager,
    worker_config: WorkerConfig,
    upstream_table: StrongEntity,
    deleted_data: Iterable[tuple[int, datetime]],
    dlq_stream_name: StreamName,
    *,
    downstream_deletions_stream_name: StreamName = StreamName.DOWNSTREAM_DELETIONS,
) -> None:
    events: list[Event] = []
    downstream_bases: tuple[AnonymousDownstreamDeletionData, ...] = (
        DownstreamDeletionMapping[upstream_table]
    )
    for deleted_entry in deleted_data:
        events.extend(
            Event(
                name=EventName.ORPHANED_COMMENT_DELETE,
                payload=DownstreamDeletionData(
                    foreign_key=deleted_entry[0], deleted_at=deleted_entry[1], **base
                ),  # type: ignore
                side_effects=EventSideEffects(),  # type: ignore
            )
            for base in downstream_bases
        )

    dispatch_coroutine = lambda: event_stream_manager.stream_events(
        events, downstream_deletions_stream_name
    )
    try:
        await execute_with_redis_retries(worker_config, dispatch_coroutine)
    except Exception:
        dispatch_coroutine = lambda: event_stream_manager.stream_events(
            events, dlq_stream_name
        )
        await execute_with_redis_retries(worker_config, dispatch_coroutine)


async def dispatch_downstream_counter_decrements(
    event_stream_manager: EventStreamManager,
    worker_config: WorkerConfig,
    deleted_entity: StrongEntity,
    deletion_author_event_id: int,
    dlq_stream_name: StreamName,
    *,
    downstream_decrements_stream_name: StreamName = StreamName.DOWNSTREAM_COUNTER_DECREMENTS,
) -> None:
    downstream_counter_data: tuple[t_downstream_counter_event_metadata, ...] | None = (
        DOWNSTREAM_DECREMENT_MAPPING.get(deleted_entity, None)
    )
    if not downstream_counter_data:
        raise ValueError(
            f"Unsupported downstream counter decrement for upstream entity: {deleted_entity}"
        )

    events: list[Event] = [
        Event(
            name=event_name,
            payload=DownstreamCounterDecrementData(
                deletion_author_event_id=deletion_author_event_id,
                affected_column_name=foreign_key_column,
                hashmap_name=hashmap_name,
                affected_table_name=deleted_entity,
            ),  # type: ignore
            side_effects=EventSideEffects(),  # type: ignore
        )
        for (event_name, foreign_key_column, hashmap_name) in downstream_counter_data
    ]

    dispatch_coroutine = lambda: event_stream_manager.stream_events(
        events, downstream_decrements_stream_name
    )
    try:
        await execute_with_redis_retries(worker_config, dispatch_coroutine)
    except Exception:
        dispatch_coroutine = lambda: event_stream_manager.stream_events(
            events, dlq_stream_name
        )
        await execute_with_redis_retries(worker_config, dispatch_coroutine)


async def emit_downstream_counter_decrement_updates(
    redis: Redis,
    deltas: Iterable[tuple[str, int]],
    hashmap_name: str,
    hash_key_prefix: StrongEntity,
) -> None:
    async with redis.pipeline() as pipeline:
        for delta in deltas:
            pipeline.hincrby(
                hashmap_name,
                derive_cache_key(hash_key_prefix, delta[0]),
                -delta[1],
            )
        await pipeline.execute()
