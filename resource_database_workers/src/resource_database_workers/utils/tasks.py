from datetime import datetime
import time
from typing import Any, Iterable

from auxillary.utils import cache_repr
from redis.asyncio import Redis

from psycopg import AsyncConnection
from psycopg.sql import Composed
from psycopg.rows import dict_row
from psycopg.errors import OperationalError, LockNotAvailable, InternalError, Error

from resource_auxillary.datastructures.database import StrongEntity
from resource_auxillary.events import Event
from resource_auxillary.strings import NAME_SEPERATOR, EventName, StreamName

from resource_database_workers.config.config import AppConfig
from resource_database_workers.datastructures.exceptions import (
    RecoverableDatabaseException,
    UnrecoverableDatabaseException,
)
from resource_database_workers.utils.strings import derive_retry_batch_name
from resource_database_workers.src.resource_database_workers.utils.sql_templates import (
    prepare_updation_sql,
)
from resource_database_workers.datastructures.downstream import (
    DownstreamDeletionMapping,
    AnonymousDownstreamDeletionData,
    DownstreamDeletionData,
)


async def dispatch_to_retrier(
    config: AppConfig,
    worker_redis: Redis,
    counter_group: str,
    counter_data: dict[int, int],
    *,
    current_retry_count: int = 0,
) -> None:
    batch_name: str = derive_retry_batch_name(
        counter_group, current_retry_count + 1, time.time()
    )
    async with worker_redis.pipeline(transaction=True) as pipeline:
        pipeline.rpush(config.WORKER.COUNTER_RETRY_REGISTRY_NAME, batch_name)
        pipeline.hset(batch_name, mapping=counter_data)
        await pipeline.execute()


async def flush_counter_updates(
    conn: AsyncConnection,
    counter_group: str,
    counters: dict[int, int],
) -> None:
    table, column = counter_group.split(NAME_SEPERATOR)
    updation_sql: Composed = prepare_updation_sql(table, column, "id_", counters)
    async with conn.cursor(row_factory=dict_row) as cursor:
        try:
            await cursor.execute(updation_sql)
            await conn.commit()
        except (OperationalError, LockNotAvailable, InternalError):
            # Transient, possibly recoverable errors
            await conn.rollback()
            raise RecoverableDatabaseException()
        except Error:
            # Unrecoverable databse errors
            await conn.rollback()
            raise UnrecoverableDatabaseException()


def generate_downstream_event_payload(
    foreign_key_column: str, foreign_key: int, orphan_table: str, deleted_at: datetime
) -> dict[str, Any]:
    return {
        "foreign_key_column": foreign_key_column,
        "foreign_key": foreign_key,
        "orphan_table": orphan_table,
        "deleted_at": deleted_at,
    }


async def dispatch_downstream_events(
    redis: Redis,
    upstream_table: StrongEntity,
    deleted_data: Iterable[tuple[int, datetime]],
) -> None:
    events: list[Event] = []
    downstream_bases: tuple[AnonymousDownstreamDeletionData, ...] = (
        DownstreamDeletionMapping[upstream_table]
    )
    for deleted_entry in deleted_data:
        events.extend(
            Event(
                name=EventName.ORPHANED_COMMENT_DELETE,
                payload=DownstreamDeletionData(
                    foreign_key=deleted_entry[0], deleted_at=deleted_entry[1], **base
                ),  # type: ignore
                side_effects=EventSideEffects(),  # type: ignore
            )
            for base in downstream_bases
        )

    async with redis.pipeline() as pipeline:
        for event in events:
            pipeline.xadd(
                StreamName.DOWNSTREAM_DELETIONS,
                cache_repr(event),
            )
        await pipeline.execute()
