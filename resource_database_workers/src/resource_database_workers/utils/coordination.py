import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
import random
from typing import Iterable
from uuid import uuid4

from psycopg import AsyncConnection, sql
from psycopg.errors import IntegrityError

from redis.asyncio import Redis

from resource_auxillary.datastructures.database import EventLiteral

from resource_database_workers.utils.sql_templates import (
    prepare_batch_dedup_sql,
    prepare_single_dedup_sql,
    prepare_temp_table_sql,
    prepare_weak_insertion_copy_sql,
)


@asynccontextmanager
async def locked_operation(redis: Redis, lock_name: str):
    try:
        yield
    finally:
        await redis.delete(lock_name)


async def dedup_insert_event(
    conn: AsyncConnection, event_id: int, acknowledgement_time: datetime | None = None
) -> bool:
    dedup_insertion_statement: sql.Composed = prepare_single_dedup_sql(
        event_id, acknowledgement_time
    )
    try:
        async with conn.transaction():
            await conn.execute(dedup_insertion_statement)
        return True
    except IntegrityError:
        await conn.rollback()
        return False


async def batch_dedup_insert_events(
    conn: AsyncConnection,
    event_ids: Iterable[int],
    acknowledgement_time: datetime | None = None,
) -> tuple[int, ...]:
    acknowledgement_time = acknowledgement_time or datetime.now()
    temp_table_name: str = f"_temp_{uuid4().hex}_{acknowledgement_time.isoformat()}"

    await conn.execute(
        prepare_temp_table_sql(temp_table_name, EventLiteral.EVENTS_TABLE_NAME)
    )
    async with conn.cursor() as cursor:
        async with cursor.copy(
            prepare_weak_insertion_copy_sql(
                temp_table_name,
                EventLiteral.EVENT_ID_COLUMN_NAME,
                EventLiteral.EVENT_TIMESTAMP_COLUMN_NAME,
            )
        ) as copy:
            for event_id in event_ids:
                await copy.write_row((event_id, acknowledgement_time))
        await cursor.execute(prepare_batch_dedup_sql(temp_table_name))
        return tuple(i[0] for i in await cursor.fetchall())


def calculate_exponential_backoff_time(
    cap: float, base: float, attempt: int, *, exponential: int = 2
) -> float:
    return min(cap, base * exponential**attempt)


async def exponential_jittered_backoff(
    cap: float, base: float, attempt: int, *, exponential: int = 2
) -> None:
    await asyncio.sleep(
        random.uniform(
            0,
            calculate_exponential_backoff_time(
                cap, base, attempt, exponential=exponential
            ),
        )
    )
