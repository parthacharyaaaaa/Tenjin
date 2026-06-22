from datetime import datetime

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from resource_database_workers.utils.sql_templates import prepare_deltas_selection


async def select_author_deltas(
    conn: AsyncConnection,
    deletion_time: datetime,
    limit: int,
    offset: int,
    author_column: str,
    table: str,
    identifier_column: str,
) -> list[dict[str, int]]:
    selection_statement = prepare_deltas_selection(
        author_column, table, deletion_time, limit, offset, identifier_column
    )
    async with conn.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(selection_statement)
        return await cursor.fetchall()
