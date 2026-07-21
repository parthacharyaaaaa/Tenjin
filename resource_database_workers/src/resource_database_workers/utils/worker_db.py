from typing import Any, Callable, Coroutine

from psycopg import AsyncConnection

from resource_database_workers.config.constants import POTENTIAL_TRANSIENT_ERRORS


async def retried_event_database_processing(
    conn: AsyncConnection,
    attempts: int,
    database_callable_coroutine: Callable[[], Coroutine[Any, Any, Any]],
) -> Exception | None:
    exception: Exception | None = None
    for _attempt in range(attempts):
        try:
            await database_callable_coroutine()
            await conn.commit()
            exception = None
            break
        except POTENTIAL_TRANSIENT_ERRORS as e:
            exception = e
            await conn.rollback()
        except Exception as e:
            # Ideally a subclass of psycopg.errors.Error,
            # but Python errors are also non-transient
            exception = e
            await conn.rollback()
            break
    return exception
