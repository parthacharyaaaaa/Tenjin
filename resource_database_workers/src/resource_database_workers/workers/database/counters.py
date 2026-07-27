from psycopg import AsyncConnection
from psycopg.sql import Composed
from psycopg.errors import OperationalError, LockNotAvailable, InternalError, Error

from resource_auxillary.datastructures.database import (
    GenericLiterals,
)
from resource_auxillary.strings import NAME_SEPERATOR

from resource_database_workers.datastructures.exceptions import (
    RecoverableDatabaseException,
    UnrecoverableDatabaseException,
)
from resource_database_workers.src.resource_database_workers.utils.sql_templates import (
    prepare_updation_sql,
)


async def flush_counter_updates(
    conn: AsyncConnection,
    counter_group: str,
    counters: dict[int, int],
) -> None:
    table, column = counter_group.split(NAME_SEPERATOR)[:2]
    updation_sql: Composed = prepare_updation_sql(
        table, column, GenericLiterals.ID, counters
    )
    async with conn.transaction():
        try:
            await conn.execute(updation_sql)
            await conn.commit()
        except (OperationalError, LockNotAvailable, InternalError):
            # Transient, possibly recoverable errors
            raise RecoverableDatabaseException()
        except Error:
            # Unrecoverable databse errors
            raise UnrecoverableDatabaseException()
