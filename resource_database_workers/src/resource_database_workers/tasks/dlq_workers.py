import asyncio
from typing import Any, Sequence

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from psycopg.sql import Composed

from auxillary.utils import json_repr

from redis.asyncio import Redis

from resource_auxillary.events import (
    CacheUpdate,
    CounterUpdate,
    IntentUpdate,
    StreamedEvent,
)
from resource_auxillary.event_processing.db_qos import (
    db_execute_with_retries,
    dedup_insert_event,
)
from resource_auxillary.event_processing.post_processing import acknowledge_event
from resource_auxillary.event_processing.qos import execute_with_redis_retries
from resource_auxillary.datastructures.database import SideEffectType
from resource_auxillary.strings import EventName, StreamName

from resource_database_workers.datastructures.dead_counter_batch import DeadCounterBatch
from resource_database_workers.config.config import AppConfig
from resource_database_workers.config.sub_config import WorkerConfig


def get_dlq_insertion_parameters(event: StreamedEvent) -> tuple[Any, ...]:
    if event.name == EventName.DLQ_COUNTER:
        dead_counter_batch: DeadCounterBatch = (
            DeadCounterBatch.construct_from_event_payload(event.payload)
        )
        return (
            dead_counter_batch.table,
            dead_counter_batch.column,
            dead_counter_batch.failure_time,
            dead_counter_batch.counters,
        )
    elif event.name == EventName.DLQ_SIDE_EFFECTS:
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
    else:  # Standard failed StreamedEvent
        return (event.event_id, json_repr(event))


async def _acknowledge_dlq_event(
    redis: Redis,
    worker_config: WorkerConfig,
    dlq_event: StreamedEvent,
    stream_name: StreamName,
    group_name: str,
) -> None:
    ack_coroutine = lambda: acknowledge_event(
        redis, [dlq_event], stream_name, group_name
    )
    await execute_with_redis_retries(worker_config, ack_coroutine)


async def _insert_dlq_record(
    connection: AsyncConnection,
    composed_statement: Composed,
    insertion_parameters: Sequence[Any],
) -> None:
    await connection.execute(composed_statement, insertion_parameters)
    await connection.commit()


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
                await _acknowledge_dlq_event(
                    redis, config.WORKER, dlq_event, stream_name, group_name
                )

            # !duplicate event
            insertion_params: tuple[Any, ...] = get_dlq_insertion_parameters(dlq_event)
            db_coroutine = lambda: _insert_dlq_record(
                conn, composed_statement, insertion_params
            )
            await db_execute_with_retries(config.WORKER, conn, db_coroutine)
            await _acknowledge_dlq_event(
                redis, config.WORKER, dlq_event, stream_name, group_name
            )

            ack_coroutine = lambda: acknowledge_event(
                redis, [dlq_event], stream_name, group_name
            )
            await execute_with_redis_retries(config.WORKER, ack_coroutine)
