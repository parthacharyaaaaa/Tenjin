import asyncio
from typing import Final

from psycopg_pool import AsyncConnectionPool
from psycopg.sql import Composed

from auxillary.utils import json_repr

from resource_auxillary.events import StreamedEvent

from resource_database_workers.datastructures.dead_counter_batch import DeadCounterBatch
from resource_database_workers.utils.sql_templates import (
    format_dlq_insertion_sql,
    format_counters_dlq_insertion_sql,
)


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
