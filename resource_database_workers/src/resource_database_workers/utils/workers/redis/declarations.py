"""Utility functions for declaring new events"""

from typing import Sequence

from redis.asyncio import Redis

from auxillary.utils import cache_repr, json_repr

from resource_auxillary.events import Event, StreamedEvent
from resource_auxillary.strings import NAME_SEPERATOR, EventName, StreamName

from resource_database_workers.config.sub_config import WorkerConfig
from resource_database_workers.datastructures.dead_counter_batch import DeadCounterBatch
from resource_database_workers.utils.workers.redis.post_processing import stream_events
from resource_database_workers.utils.workers.redis.qos import execute_with_redis_retries


async def declare_counters_event_dead(
    redis: Redis,
    dlq_stream_name: StreamName,
    counter_group: str,
    batch: dict[int, int],
    attempts: int,
) -> None:
    table, column = counter_group.split(NAME_SEPERATOR)
    dlq_counters_batch: DeadCounterBatch = DeadCounterBatch.construct_from_failed_batch(
        table, column, batch
    )
    failure_event: Event = Event(
        name=EventName.DLQ_COUNTER,
        payload=json_repr(dlq_counters_batch),
        side_effects=EventSideEffects(),  # type: ignore
    )

    xack_coroutine = lambda: redis.xadd(dlq_stream_name, cache_repr(failure_event))
    await execute_with_redis_retries(xack_coroutine, attempts)  # type: ignore[reportArgumentType]


async def declare_side_effects_event_dead(
    redis: Redis,
    worker_config: WorkerConfig,
    batch: Sequence[StreamedEvent],
    dlq_stream_name: StreamName,
    attempts: int,
) -> None:
    failure_events: tuple[Event] = tuple(
        Event(
            name=EventName.DLQ_SIDE_EFFECTS,
            payload=json_repr(event),
            side_effects=EventSideEffects(),  # type: ignore
        )
        for event in batch
    )

    dlq_coroutine = lambda: stream_events(redis, failure_events, dlq_stream_name)
    await execute_with_redis_retries(worker_config, dlq_coroutine, attempts)
