from datetime import datetime
from typing import Iterable, Literal, MutableSequence, Protocol, Sequence

from psycopg import AsyncConnection

from resource_auxillary.events import StreamedEvent

type t_action_literal = Literal["save", "vote", "subscribe"]


class BatchInsertionFunction(Protocol):
    """Batch insertion function, returning event IDs of succesfully inserted event payloads"""

    async def __call__(
        self,
        conn: AsyncConnection,
        events: Sequence[StreamedEvent],
        successfully_inserted: MutableSequence[int],
        action: t_action_literal | None,
        /,
    ) -> None: ...


class BatchDeletionFunction(Protocol):
    async def __call__(
        self,
        conn: AsyncConnection,
        table: str,
        identifier_column: str,
        deletion_data: Iterable[tuple[int, datetime, int]],
        /,
    ) -> None: ...


class BatchDownstreamDeletionFunction(Protocol):
    async def __call__(
        self,
        conn: AsyncConnection,
        parent_foreign_key: int,
        orphan_table: str,
        foreign_key_column: str,
        deletion_time: datetime | None = None,
        /,
    ) -> None: ...
