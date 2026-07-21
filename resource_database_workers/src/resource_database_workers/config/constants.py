import re
from typing import Final

from psycopg import errors as psycopg_errors

DOMAIN_REGEX: Final[re.Pattern] = re.compile(
    r"^((?!-)[A-Za-z0-9-]{1, 63}(?<!-)\\.)+[A-Za-z]{2, 6}$"
)

POTENTIAL_TRANSIENT_ERRORS: Final[tuple[type[psycopg_errors.Error], ...]] = (
    psycopg_errors.OperationalError,
    psycopg_errors.InternalError,
)
