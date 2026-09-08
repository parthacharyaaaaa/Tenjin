from typing import TypeVar
from datetime import datetime
from typing import Any, Callable, Coroutine, Iterable
from uuid import uuid4

from psycopg import AsyncConnection, sql
from psycopg.errors import IntegrityError

from resource_auxillary.constants import POTENTIAL_TRANSIENT_ERRORS
from resource_auxillary.coordination import exponential_jittered_backoff
from resource_auxillary.datastructures.database import EventLiteral
from resource_auxillary.strings import EventName
from resource_auxillary.templates.sql import (
    prepare_batch_dedup_sql,
    prepare_single_dedup_sql,
    prepare_temp_table_sql,
    prepare_weak_insertion_copy_sql,
)
from resource_auxillary.typing import SupportsExponentialJitteredRetryPolicy

T = TypeVar("T")


async def db_execute_with_retries(
    retry_policy: SupportsExponentialJitteredRetryPolicy,
    connection: AsyncConnection,
    db_coroutine: Callable[[], Coroutine[Any, Any, T]],
    attempts: int | None = None,
) -> T:
    attempts = attempts if attempts is not None else retry_policy.MAX_RETRIES
    for _attempt in range(1, attempts + 1):
        try:
            return await db_coroutine()
        except POTENTIAL_TRANSIENT_ERRORS as pt_err:
            await connection.rollback()
            if _attempt == attempts:
                raise pt_err
            await exponential_jittered_backoff(
                retry_policy.MAXIMUM_BACKOFF_INTERVAL,
                retry_policy.BASE_BACKOFF_INTERVAL,
                _attempt,
                exponential=retry_policy.BACKOFF_EXPONENTIAL,
            )
        except Exception as e:
            await connection.rollback()
            raise
    raise AssertionError("Retry loop exited unexpectedly")


async def dedup_insert_event(
    conn: AsyncConnection,
    event_id: int,
    event_name: EventName,
    acknowledgement_time: datetime | None = None,
) -> bool:
    dedup_insertion_statement: sql.Composed = prepare_single_dedup_sql(
        event_id, event_name, acknowledgement_time
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
    event_name: EventName,
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
                EventLiteral.EVENT_NAME_COLUMN_NAME,
            )
        ) as copy:
            for event_id in event_ids:
                await copy.write_row((event_id, acknowledgement_time, event_name))
        await cursor.execute(prepare_batch_dedup_sql(temp_table_name))
        return tuple(i[0] for i in await cursor.fetchall())
