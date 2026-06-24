from psycopg import AsyncConnection
from resource_auxillary.datastructures.database import ForeignKeyColumnLiteral

from resource_database_workers.utils.sql_templates import prepare_deltas_selection


async def select_decrement_deltas(
    conn: AsyncConnection,
    foreign_key_column: ForeignKeyColumnLiteral,
    limit: int,
    offset: int,
    table: str,
    deletion_author_event_id: int,
) -> list[tuple[str, int]]:
    selection_statement = prepare_deltas_selection(
        foreign_key_column,
        table,
        deletion_author_event_id,
        limit,
        offset,
    )
    async with conn.cursor() as cursor:
        await cursor.execute(selection_statement)
        return await cursor.fetchall()
