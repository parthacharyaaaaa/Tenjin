from typing import Final
from psycopg import errors as psycopg_errors

POTENTIAL_TRANSIENT_ERRORS: Final[tuple[type[psycopg_errors.Error], ...]] = (
    psycopg_errors.OperationalError,
    psycopg_errors.InternalError,
)
