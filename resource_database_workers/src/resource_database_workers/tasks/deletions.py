from datetime import datetime
from typing import Iterable

from psycopg import AsyncConnection
from psycopg.sql import Composed

from resource_database_workers.utils.sql_templates import (
    prepare_orphan_deletion,
    prepare_strong_deletion_sql,
)


async def soft_delete_strong_entity(
    conn: AsyncConnection,
    table: str,
    identifier_column: str,
    deletion_data: Iterable[tuple[int, datetime]],
) -> None:
    deletion_statement: Composed = prepare_strong_deletion_sql(
        table, identifier_column, deletion_data
    )
    await conn.execute(deletion_statement)


async def downstream_soft_delete_strong_entity(
    conn: AsyncConnection,
    parent_foreign_key: int,
    orphan_table: str,
    foreign_key_column: str,
    deletion_time: datetime | None = None,
) -> None:
    deletion_time = deletion_time or datetime.now()
    deletion_statement: Composed = prepare_orphan_deletion(
        orphan_table, foreign_key_column, parent_foreign_key, deletion_time
    )
    await conn.execute(deletion_statement)
