import asyncio
from typing import Final

from psycopg_pool import AsyncConnectionPool
from psycopg.sql import Composed

from auxillary.utils import json_repr

from resource_auxillary.events import (
    CacheUpdate,
    CounterUpdate,
    IntentUpdate,
    StreamedEvent,
)
from resource_auxillary.datastructures.database import SideEffectType

from resource_database_workers.datastructures.dead_counter_batch import DeadCounterBatch
from resource_database_workers.utils.sql_templates import (
    format_dlq_insertion_sql,
    format_counters_dlq_insertion_sql,
    format_failed_side_effects_sql,
)

# TODO: Add abstraction for deduplication and retry logic


async def dlq_consumer(
    pool: AsyncConnectionPool, queue: asyncio.Queue[StreamedEvent]
) -> None:
    insertion_sql: Final[Composed] = format_dlq_insertion_sql()
    while True:
        dlq_event: StreamedEvent = await queue.get()
        async with pool.connection() as conn:
            await conn.execute(
                insertion_sql, (dlq_event.event_id, json_repr(dlq_event))
            )
            await conn.commit()


async def counters_dlq_consumer(
    pool: AsyncConnectionPool, queue: asyncio.Queue[DeadCounterBatch]
) -> None:
    insertion_sql: Final[Composed] = format_counters_dlq_insertion_sql()
    while True:
        batch: DeadCounterBatch = await queue.get()
        async with pool.connection() as conn:
            await conn.execute(
                insertion_sql,
                (batch.table, batch.column, batch.failure_time, batch.counters),
            )
            await conn.commit()


async def side_effects_dlq_consumer(
    pool: AsyncConnectionPool, queue: asyncio.Queue[StreamedEvent]
) -> None:
    insertion_sql: Final[Composed] = format_failed_side_effects_sql()
    while True:
        failed_side_effects_parent_event: StreamedEvent = await queue.get()
        side_effect_groups: tuple[
            tuple[
                SideEffectType, tuple[CounterUpdate | IntentUpdate | CacheUpdate, ...]
            ],
            ...,
        ] = (
            (
                SideEffectType.CACHE_INVALIDATION,
                failed_side_effects_parent_event.side_effects.cache_invalidations,
            ),
            (
                SideEffectType.COUNTER_UPDATE,
                failed_side_effects_parent_event.side_effects.counter_updates,
            ),
            (
                SideEffectType.INTENT_INVALIDATION,
                failed_side_effects_parent_event.side_effects.intent_updates,
            ),
        )
        async with pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.executemany(
                    insertion_sql,
                    (
                        (
                            failed_side_effects_parent_event.event_id,
                            side_effect_type.value,
                            json_repr(side_effect),
                        )
                        for (side_effect_type, side_effects) in side_effect_groups
                        for side_effect in side_effects
                    ),
                )
                await conn.commit()
