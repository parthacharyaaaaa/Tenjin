from typing import Final, LiteralString
from uuid import uuid4

from auth_server.strings import GENERIC_SEPARATOR

STREAM_ID_ORDERING_ERROR_STRING: Final[LiteralString] = (
    "The ID specified in XADD is equal or smaller than the target stream top item"
)


def generate_lease_identity(*args: str) -> str:
    return GENERIC_SEPARATOR.join((*args, uuid4().hex))
