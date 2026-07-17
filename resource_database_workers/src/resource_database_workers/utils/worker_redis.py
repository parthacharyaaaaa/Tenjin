import asyncio
import time
from typing import Sequence

from redis.asyncio import Redis
from redis.exceptions import RedisError, ExceptionType

from auxillary.utils import cache_repr, json_repr

from resource_auxillary.events import Event, StreamedEvent
from resource_auxillary.strings import NAME_SEPERATOR, EventName, StreamName

from resource_database_workers.config.config import AppConfig
from resource_database_workers.datastructures.dead_counter_batch import DeadCounterBatch
from resource_database_workers.utils.coordination import (
    atomic_emit_side_effects,
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
