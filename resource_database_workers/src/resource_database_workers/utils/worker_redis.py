import asyncio
import time
from typing import Any, Callable, Coroutine, Iterable, Mapping, Sequence

import orjson

from redis.asyncio import Redis
from redis.asyncio.client import Pipeline
from redis.exceptions import RedisError, ExceptionType

from auxillary.utils import cache_repr, json_repr

from resource_auxillary.cache import NF_MAPPING
from resource_auxillary.events import (
    CacheUpdate,
    CounterUpdate,
    Event,
    IntentUpdate,
    StreamedEvent,
)
from resource_auxillary.strings import NAME_SEPERATOR, EventName, StreamName

from resource_database_workers.config.config import AppConfig
from resource_database_workers.datastructures.dead_counter_batch import DeadCounterBatch
from resource_database_workers.src.resource_database_workers.config.sub_config import (
    WorkerConfig,
)
from resource_database_workers.src.resource_database_workers.utils.coordination import (
    exponential_jittered_backoff,
)
from resource_database_workers.src.resource_database_workers.utils.lua_commands import (
    CONDITIIONAL_DELETE_TARGET_INTENT_TEMPLATE,
    CONDITIONAL_COUNTER_DECREMENT_TEMPLATE,
)


async def populate_events_batch_from_queue(
    config: AppConfig,
    queue: asyncio.Queue[tuple[StreamedEvent, ...]],
    reference_time: float,
    batch: list[StreamedEvent],
) -> None:
    while True:
        if not (
            (len(batch) >= config.WORKER.IQ_CONSUMER_BATCH_SIZE_QUOTA)
            or time.monotonic() - reference_time
            > config.WORKER.IQ_CONSUMER_BASE_WAITING_TIME
        ):
            try:
                new_entries: tuple[StreamedEvent, ...] = await asyncio.wait_for(
                    queue.get(), config.WORKER.IQ_CONSUMER_GET_TIMEOUT
                )
                if not batch:
                    reference_time = time.monotonic()
                batch.extend(new_entries)
            except asyncio.TimeoutError:
                await asyncio.sleep(config.WORKER.IQ_CONSUMER_SLEEP_INTERVAL)
            continue

        if not batch:
            await asyncio.sleep(config.WORKER.IQ_CONSUMER_SLEEP_INTERVAL)
            reference_time = time.monotonic()
            continue


async def execute_with_redis_retries(
    worker_config: WorkerConfig,
    redis_coroutine: Callable[[], Coroutine[Any, Any, Any]],
    attempts: int,
) -> Any:
    exception: Exception | None = None
    for _attempt in range(1, attempts + 1):
        try:
            return await redis_coroutine()
        except RedisError as redis_error:
            exception = redis_error
            if redis_error.error_type == ExceptionType.NETWORK:
                await exponential_jittered_backoff(
                    worker_config.MAXIMUM_BACKOFF_INTERVAL,
                    worker_config.BASE_BACKOFF_INTERVAL,
                    _attempt,
                    exponential=worker_config.BACKOFF_EXPONENTIAL,
                )
                continue

            break
        except Exception as e:
            exception = e
            break

    if exception:
        raise exception


async def dlq_aware_process_events(
    redis: Redis,
    worker_config: WorkerConfig,
    events: Sequence[StreamedEvent],
    redis_coroutine: Callable[[], Coroutine[Any, Any, Any]],
    attempts: int,
    event_stream_name: StreamName,
    group_name: str,
    dlq_stream_name: StreamName,
) -> Any:
    """
    DLQ-aware event processing helper with retries
    """
    try:
        await execute_with_redis_retries(worker_config, redis_coroutine, attempts)
    except Exception as e:
        dlq_attempts: int = (
            1 if getattr(e, "error_type", None) == ExceptionType.NETWORK else attempts
        )
        await declare_dead_with_retries(
            redis,
            worker_config,
            events,
            event_stream_name,
            group_name,
            dlq_stream_name,
            dlq_attempts,
        )


async def amortize_event(
    redis: Redis,
    events: Sequence[StreamedEvent],
    event_stream_name: StreamName,
    group_name: str,
    dlq_stream_name: StreamName,
) -> None:
    async with redis.pipeline(transaction=True) as pipeline:
        for event in events:
            pipeline.xack(event_stream_name, group_name, event.event_id)
            pipeline.xadd(dlq_stream_name, cache_repr(event), id=event.event_id)
        await pipeline.execute()


async def acknowledge_event(
    redis: Redis,
    events: Sequence[StreamedEvent],
    event_stream_name: StreamName,
    group_name: str,
) -> None:
    async with redis.pipeline(transaction=True) as pipeline:
        for event in events:
            pipeline.xack(event_stream_name, group_name, event.event_id)
        await pipeline.execute()


async def stream_events(
    redis: Redis, events: Iterable[Event], stream_name: StreamName
) -> None:
    async with redis.pipeline(transaction=True) as pipeline:
        for event in events:
            await pipeline.xadd(stream_name, cache_repr(event))
        await pipeline.execute()


async def trim_duplicate_events(
    redis: Redis,
    batch: list[StreamedEvent],
    fresh_event_ids: Sequence[int],
    stream_name: StreamName,
    group_name: str,
) -> None:
    async with redis.pipeline() as pipeline:
        for event in batch.copy():
            if event.event_id not in fresh_event_ids:
                batch.remove(event)
                pipeline.xack(stream_name, group_name, event.event_id)
        await pipeline.execute()


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


async def retrieve_counter_group_names(redis: Redis, registry_name: str) -> set[str]:
    return {
        str(i)
        for i in (
            await redis.smembers(registry_name)  # type: ignore[reportGeneralTypeIssues]
        )
    }


async def reflect_processed_counters(
    server_redis: Redis, counter_group: str, counters: Mapping[str, int]
) -> None:
    async with server_redis.pipeline(transaction=True) as pipeline:
        for k, v in counters.items():
            pipeline.eval(
                CONDITIONAL_COUNTER_DECREMENT_TEMPLATE, 2, counter_group, k, v
            )
        await pipeline.execute()


async def atomic_emit_side_effects(
    redis: Redis, events: Sequence[StreamedEvent]
) -> None:
    async with redis.pipeline(transaction=True) as pipeline:
        _emit_intent_invalidations(
            pipeline, (i.side_effects.intent_updates for i in events)
        )
        _emit_counter_side_effects(
            pipeline, (i.side_effects.counter_updates for i in events)
        )
        _emit_cache_invalidation_side_effects(
            pipeline, (i.side_effects.cache_invalidations for i in events)
        )
        await pipeline.execute()


def _emit_intent_invalidations(
    pipeline: Pipeline, intent_updates: Iterable[Sequence[IntentUpdate]]
) -> None:
    for event_intent_updates in intent_updates:
        for resource_intent_update in event_intent_updates:
            pipeline.eval(
                CONDITIIONAL_DELETE_TARGET_INTENT_TEMPLATE,
                1,
                resource_intent_update.intent_name,
                resource_intent_update.intent_value,
            )


def _emit_cache_invalidation_side_effects(
    pipeline: Pipeline, cache_side_effects: Iterable[Sequence[CacheUpdate]]
) -> None:
    for event_cache_invalidations in cache_side_effects:
        for resource_cache_invalidation in event_cache_invalidations:
            if resource_cache_invalidation.operation == "invalidate":
                pipeline.delete(resource_cache_invalidation.cache_key)
                continue
            if resource_cache_invalidation.resource_type == "mapping":
                pipeline.hset(resource_cache_invalidation.cache_key, mapping=NF_MAPPING)
                # TODO: Remove magic numbers for expiry values
                pipeline.expire(resource_cache_invalidation.cache_key, 60)
            else:
                pipeline.set(
                    resource_cache_invalidation.cache_key,
                    orjson.dumps(NF_MAPPING),
                    ex=60,
                )


def _emit_counter_side_effects(
    pipeline: Pipeline, counter_side_effects: Iterable[Sequence[CounterUpdate]]
) -> None:
    for event_side_effects in counter_side_effects:
        for side_effect in event_side_effects:
            pipeline.hincrby(
                side_effect.counter_group, side_effect.cache_key, side_effect.delta
            )


async def declare_dead_with_retries(
    redis: Redis,
    worker_config: WorkerConfig,
    batch: Sequence[StreamedEvent],
    stream_name: StreamName,
    group_name: str,
    dead_letter_stream_name: StreamName,
    attempts: int,
) -> None:
    """
    Thin wrapper over sibling utility functions to declare an event as dead
    """
    coro = lambda: amortize_event(
        redis, batch, stream_name, group_name, dead_letter_stream_name
    )
    await execute_with_redis_retries(worker_config, coro, attempts)


async def ack_with_retries(
    redis: Redis,
    worker_config: WorkerConfig,
    batch: Sequence[StreamedEvent],
    stream_name: StreamName,
    group_name: str,
    dead_letter_stream_name: StreamName,
    attempts: int,
) -> None:
    """
    Thin wrapper over sibling utility functions to acknowledge an event
    """
    coro = lambda: acknowledge_event(redis, batch, stream_name, group_name)
    await dlq_aware_process_events(
        redis,
        worker_config,
        batch,
        coro,
        attempts,
        stream_name,
        group_name,
        dead_letter_stream_name,
    )


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


async def dlq_aware_emit_side_effects(
    redis: Redis,
    worker_config: WorkerConfig,
    batch: Sequence[StreamedEvent],
    dead_letter_stream_name: StreamName,
    attempts: int,
) -> None:
    """
    Thin wrapper over sibling utility functions to emit event side-effects
    """
    coro = lambda: atomic_emit_side_effects(redis, batch)

    try:
        await execute_with_redis_retries(worker_config, coro, attempts)
    except Exception as e:
        dlq_attempts: int = (
            1 if getattr(e, "error_type", None) == ExceptionType.NETWORK else attempts
        )
        await declare_side_effects_event_dead(
            redis, worker_config, batch, dead_letter_stream_name, dlq_attempts
        )
