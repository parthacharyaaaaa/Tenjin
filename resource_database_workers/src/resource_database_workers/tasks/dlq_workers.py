import asyncio
from typing import Any, Sequence

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from psycopg.sql import Composed
from psycopg import errors as psycopg_errors

from auxillary.utils import json_repr

from redis.asyncio import Redis
from redis.exceptions import RedisError, ExceptionType
from resource_auxillary.events import (
    CacheUpdate,
    CounterUpdate,
    IntentUpdate,
    StreamedEvent,
)
from resource_auxillary.datastructures.database import SideEffectType
from resource_auxillary.strings import EventName, StreamName

from resource_database_workers.datastructures.dead_counter_batch import DeadCounterBatch
from resource_database_workers.config.config import AppConfig
from resource_database_workers.utils.coordination import dedup_insert_event


def get_dlq_insertion_parameters(event: StreamedEvent) -> tuple[Any, ...]:
    if event.name == EventName.DLQ_STANDARD:
        return (event.event_id, json_repr(event))
    elif event.name == EventName.DLQ_COUNTER:
        dead_counter_batch: DeadCounterBatch = (
            DeadCounterBatch.construct_from_event_payload(event.payload)
        )
        return (
            dead_counter_batch.table,
            dead_counter_batch.column,
            dead_counter_batch.failure_time,
            dead_counter_batch.counters,
        )
    else:
        side_effect_groups: tuple[
            tuple[
                SideEffectType, tuple[CounterUpdate | IntentUpdate | CacheUpdate, ...]
            ],
            ...,
        ] = (
            (
                SideEffectType.CACHE_INVALIDATION,
                event.side_effects.cache_invalidations,
            ),
            (
                SideEffectType.COUNTER_UPDATE,
                event.side_effects.counter_updates,
            ),
            (
                SideEffectType.INTENT_INVALIDATION,
                event.side_effects.intent_updates,
            ),
        )
        return tuple(
            (event.event_id, side_effect_type.value, json_repr(side_effect))
            for (side_effect_type, side_effects) in side_effect_groups
            for side_effect in side_effects
        )


async def _retried_insert_db_dlq_event(
    connection: AsyncConnection,
    composed_statement: Composed,
    insertion_parameters: Sequence[Any],
    attempts: int,
) -> Exception | None:
    """
    Attempt to insert a DLQ event into a DLQ table

    This coroutine NEVER fails, instead returning an optional exception encountered
    """
    exception: Exception | None = None
    for _attempt in range(attempts):
        try:
            async with connection.transaction():
                await connection.execute(composed_statement, insertion_parameters)
                await connection.commit()
            exception = None
            break
        except (psycopg_errors.OperationalError, psycopg_errors.InternalError) as exc:
            await connection.rollback()
            exception = exc
        except Exception as exc:
            # Ideally a subclass of psycopg.errors.Error,
            # but Python errors are also non-transient
            await connection.rollback()
            exception = exc
            break

    return exception


async def _retried_ack_inserted_dlq_event(
    redis: Redis, event_id: int, stream_name: str, group_name: str, attempts: int
) -> Exception | None:
    """
    Attempt to acknowledge an event in a stream as part of a consumer group

    This coroutine NEVER fails, instead returning an optional exception encountered
    """
    exception: Exception | None = None
    for _attempt in range(attempts):
        try:
            await redis.xack(stream_name, group_name, event_id)
        except RedisError as redis_exc:
            exception = redis_exc
            if redis_exc.error_type != ExceptionType.NETWORK:
                break
        except Exception as exc:
            exception = exc
            break
    return exception


async def retried_process_dlq_data(
    config: AppConfig,
    connection: AsyncConnection,
    event_id: int,
    composed_statement: Composed,
    insertion_parameters: Sequence[Any],
    redis: Redis,
    stream_name: str,
    group_name: str,
) -> None:
    """
    Insert a DLQ entry into the database and acknowledge it in its resident Redis stream
    """
    db_exception: Exception | None = await _retried_insert_db_dlq_event(
        connection, composed_statement, insertion_parameters, config.WORKER.MAX_RETRIES
    )
    if db_exception:
        raise db_exception
    redis_exception: Exception | None = await _retried_ack_inserted_dlq_event(
        redis, event_id, stream_name, group_name, config.WORKER.MAX_RETRIES
    )
    if redis_exception:
        raise redis_exception


async def dlq_consumer(
    config: AppConfig,
    stream_name: StreamName,
    pool: AsyncConnectionPool,
    redis: Redis,
    group_name: str,
    queue: asyncio.Queue[StreamedEvent],
    composed_statement: Composed,
) -> None:
    while True:
        dlq_event: StreamedEvent = await queue.get()
        async with pool.connection() as conn:
            # Apply deduplication
            if not await dedup_insert_event(conn, dlq_event.event_id):
                redis_exc: Exception | None = await _retried_ack_inserted_dlq_event(
                    redis,
                    dlq_event.event_id,
                    stream_name,
                    group_name,
                    config.WORKER.MAX_RETRIES,
                )
                if redis_exc:
                    raise redis_exc
                continue

            # !duplicate event
            insertion_params: tuple[Any, ...] = get_dlq_insertion_parameters(dlq_event)
            await retried_process_dlq_data(
                config,
                conn,
                dlq_event.event_id,
                composed_statement,
                insertion_params,
                redis,
                stream_name,
                group_name,
            )
