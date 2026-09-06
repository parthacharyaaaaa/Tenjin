from datetime import datetime
from resource_database_workers.utils.sql_templates import (
    DLQ_INSERTION_COMPOSED_STATEMENT,
)
from resource_database_workers.dependencies.annotations import ISOLATED_EVENT_QUEUE
from typing import Any, Sequence

from psycopg import AsyncConnection
from psycopg.sql import Composed

from auxillary.utils import json_repr

from resource_auxillary.events import (
    StreamedEvent,
)
from resource_auxillary.event_processing.db_qos import (
    db_execute_with_retries,
    dedup_insert_event,
)
from resource_auxillary.event_processing.qos import execute_with_redis_retries
from resource_auxillary.strings import EventName

from resource_database_workers.dependencies.annotations import (
    APP_CONFIG,
    DEAD_LETTER_STREAM_NAME,
    CONNECTION_POOL,
    GROUP_NAME,
    STATUS_PROXY,
    EVENT_STREAM_MANAGER,
)


def get_dlq_insertion_parameters(
    event: StreamedEvent,
) -> tuple[int, EventName, dict[str, Any], datetime]:
    return (
        event.event_id,
        event.name,
        json_repr(event),
        (
            event.creation_time
            if event.name in (EventName.DLQ_COUNTER, EventName.DLQ_SIDE_EFFECTS)
            else datetime.now()
        ),
    )


async def _insert_dlq_record(
    connection: AsyncConnection,
    composed_statement: Composed,
    insertion_parameters: Sequence[Any],
) -> None:
    await connection.execute(composed_statement, insertion_parameters)
    await connection.commit()


async def dlq_consumer(
    config: APP_CONFIG,
    stream_name: DEAD_LETTER_STREAM_NAME,
    pool: CONNECTION_POOL,
    event_stream_manager: EVENT_STREAM_MANAGER,
    group_name: GROUP_NAME,
    queue: ISOLATED_EVENT_QUEUE,
    status_proxy: STATUS_PROXY,
) -> None:
    while status_proxy.status_ok:
        dlq_event: StreamedEvent = await queue.get()
        async with pool.connection() as conn:
            # Apply deduplication

            if not await dedup_insert_event(conn, dlq_event.event_id, dlq_event.name):
                # Retry, but appending back to DLQ is pointless in a DLQ worker
                await execute_with_redis_retries(
                    config.WORKER,
                    lambda: event_stream_manager.acknowledge_events(
                        (dlq_event,), stream_name, group_name
                    ),
                )

            # !duplicate event
            insertion_params: tuple[Any, ...] = get_dlq_insertion_parameters(dlq_event)
            db_coroutine = lambda: _insert_dlq_record(
                conn, DLQ_INSERTION_COMPOSED_STATEMENT, insertion_params
            )
            await db_execute_with_retries(config.WORKER, conn, db_coroutine)
            await execute_with_redis_retries(
                config.WORKER,
                lambda: event_stream_manager.acknowledge_events(
                    (dlq_event,), stream_name, group_name
                ),
            )
