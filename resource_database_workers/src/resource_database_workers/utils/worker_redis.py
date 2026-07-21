import asyncio
import time
from typing import Iterable, Mapping, Sequence

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


async def declare_dead_with_retries(
    redis: Redis,
    events: Sequence[StreamedEvent],
    event_stream_name: StreamName,
    group_name: str,
    dlq_stream_name: StreamName,
    attempts: int,
) -> None:
    exception: Exception | None = None
    for _attempt in range(attempts):
        try:
            async with redis.pipeline(transaction=True) as pipeline:
                for event in events:
                    pipeline.xack(event_stream_name, group_name, event.event_id)
                    pipeline.xadd(dlq_stream_name, cache_repr(event), id=event.event_id)
                await pipeline.execute()
            exception = None
            break
        except RedisError as redis_error:
            exception = redis_error
            if redis_error.error_type == ExceptionType.NETWORK:
                continue
            break
        except Exception as e:
            exception = e
            break

    if exception:
        raise exception


async def ack_with_retries(
    redis: Redis,
    events: Sequence[StreamedEvent],
    event_stream_name: StreamName,
    group_name: str,
    attempts: int,
) -> None:
    exception: Exception | None = None
    for _attempt in range(attempts):
        try:
            async with redis.pipeline(transaction=True) as pipeline:
                for event in events:
                    pipeline.xack(event_stream_name, group_name, event.event_id)
                await pipeline.execute()
            exception = None
            break
        except RedisError as redis_error:
            exception = redis_error
            if redis_error.error_type == ExceptionType.NETWORK:
                continue
            break
        except Exception as e:
            exception = e
            break

    if exception:
        raise exception


async def emit_side_effects_with_retries(
    redis: Redis,
    events: Sequence[StreamedEvent],
    attempts: int,
    stream_name: StreamName,
    group_name: str,
) -> None:
    exception: Exception | None = None
    for _attempt in range(attempts):
        try:
            await atomic_emit_side_effects(redis, events)
        except RedisError as redis_error:
            exception = redis_error
            if redis_error.error_type == ExceptionType.NETWORK:
                continue
            break
        except Exception as e:
            exception = e
            break

    if not exception:
        return
    for event in events:
        event.name = EventName.DLQ_SIDE_EFFECTS

    dlq_declaration_attempts: int = (
        1
        if (
            isinstance(exception, RedisError)
            and exception.error_type == ExceptionType.NETWORK
        )
        else attempts
    )
    await declare_dead_with_retries(
        redis,
        events,
        stream_name,
        group_name,
        StreamName.DEAD_LETTER_QUEUE,
        dlq_declaration_attempts,
    )


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

    exception: Exception | None = None
    for _attempt in range(attempts):
        try:
            await redis.xadd(dlq_stream_name, cache_repr(failure_event))
            exception = None
            break
        except RedisError as e:
            exception = e
            if e.error_type == ExceptionType.NETWORK:
                continue
            break
        except Exception as e:
            exception = e
            break

    if exception:
        raise exception


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
