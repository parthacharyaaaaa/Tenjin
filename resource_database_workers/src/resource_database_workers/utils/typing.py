from typing import Protocol, Sequence

from psycopg import AsyncConnection

from resource_auxillary.events import Event


class BatchInsertionFunction(Protocol):
    """Batch insertion function, returning event IDs of succesfully inserted event payloads"""

    async def __call__(
        self, conn: AsyncConnection, events: Sequence[Event], /
    ) -> list[str]: ...
