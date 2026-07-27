from datetime import datetime
from typing import Any, Callable, Coroutine, Iterable
from uuid import uuid4

from psycopg import AsyncConnection
from psycopg import sql
from psycopg.errors import IntegrityError
from resource_auxillary.datastructures.database import EventLiteral

from resource_database_workers.config.constants import POTENTIAL_TRANSIENT_ERRORS

from resource_database_workers.config.sub_config import WorkerConfig
from resource_database_workers.utils.coordination import exponential_jittered_backoff
from resource_database_workers.utils.sql_templates import (
    prepare_batch_dedup_sql,
    prepare_single_dedup_sql,
    prepare_temp_table_sql,
    prepare_weak_insertion_copy_sql,
)


async def db_execute_with_retries(
    worker_config: WorkerConfig,
    connection: AsyncConnection,
    db_coroutine: Callable[[], Coroutine[Any, Any, Any]],
    attempts: int | None = None,
) -> Any:
    attempts = attempts or worker_config.MAX_RETRIES
    exception: Exception | None = None
    for _attempt in range(1, attempts + 1):
        try:
            return await db_coroutine()
        except POTENTIAL_TRANSIENT_ERRORS as pt_err:
            await connection.rollback()
            exception = pt_err
            await exponential_jittered_backoff(
                worker_config.MAXIMUM_BACKOFF_INTERVAL,
                worker_config.BASE_BACKOFF_INTERVAL,
                _attempt,
                exponential=worker_config.BACKOFF_EXPONENTIAL,
            )
        except Exception as e:
            await connection.rollback()
            exception = e
            break

    if exception:
        raise exception


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
