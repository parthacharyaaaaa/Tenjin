from contextlib import asynccontextmanager
from resource_auxillary.event_processing.event_stream_manager import EventStreamManager
from auxillary.typing_utils import SupportsMembershipCheck
from resource_auxillary.typing import SupportsExponentialJitteredRetryPolicy
from resource_auxillary.strings import EventName, StreamName
from resource_auxillary.events import Event, EventSideEffects
from resource_auxillary.event_processing.qos import execute_with_redis_retries
from resource_auxillary.event_processing.db_qos import db_execute_with_retries
from resource_auxillary.templates.sql import prepare_side_effects_processing_sql
from resource_auxillary.datastructures.database import SideEffectsLiteral
from typing import Any, TypeVar

from psycopg.connection_async import AsyncConnection
from psycopg.rows import dict_row

from pydantic import BaseModel

from resource_auxillary.datastructures.database import SideEffectsTables, EventLiteral
from resource_auxillary.templates.sql import prepare_side_effects_read_sql

T = TypeVar("T", bound=BaseModel)


async def get_side_effect_row(
    conn: AsyncConnection, side_effects_table: SideEffectsTables
) -> tuple[int, dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cursor:
        while True:
            await cursor.execute(prepare_side_effects_read_sql(side_effects_table))
            result: dict[str, Any] | None = await cursor.fetchone()
            if result:
                break  # TODO: Add backoff periods
        return (
            result[EventLiteral.EVENT_ID_COLUMN_NAME],
            result[SideEffectsLiteral.SIDE_EFFECTS_PAYLOAD],
        )


async def mark_side_effect_row_processed(
    conn: AsyncConnection, side_effects_table: SideEffectsTables, parent_event_id: int
) -> None:
    await conn.execute(
        prepare_side_effects_processing_sql(side_effects_table, parent_event_id)
    )


@asynccontextmanager
async def side_effects_processing_context(
    conn: AsyncConnection,
    side_effects_table: SideEffectsTables,
    event_stream_manager: EventStreamManager,
    dead_letter_stream_name: StreamName,
    event_id: int,
    retry_policy: SupportsExponentialJitteredRetryPolicy,
    *,
    ignored_exceptions: SupportsMembershipCheck[Exception] | None = None,
):
    ignored_exceptions = ignored_exceptions if ignored_exceptions else tuple()
    exception: Exception | None = None
    try:
        yield
    except Exception as e:
        exception = e
        if e in ignored_exceptions:  # Propagate upwards
            raise e
        # Stream as a dead event
        dead_event: Event = Event(
            name=EventName.DLQ_SIDE_EFFECTS,
            payload={"event_id": event_id},
            side_effects=EventSideEffects(),
        )
        emission_coroutine = lambda: event_stream_manager.stream_events(
            (dead_event,), dead_letter_stream_name
        )
        await execute_with_redis_retries(retry_policy, emission_coroutine)
    finally:
        # Mark outbox side-effect record as 'emitted'/'processed' only
        # when no exception is raised
        if exception is not None:
            outbox_processing_coroutine = lambda: mark_side_effect_row_processed(
                conn, side_effects_table, event_id
            )
            await db_execute_with_retries(
                retry_policy, conn, outbox_processing_coroutine
            )
