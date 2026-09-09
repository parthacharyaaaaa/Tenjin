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
