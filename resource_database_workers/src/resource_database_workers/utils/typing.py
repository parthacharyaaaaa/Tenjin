from typing import Literal, Protocol, Sequence

from psycopg import AsyncConnection

from resource_auxillary.events import Event

type t_action_literal = Literal["save", "vote", "subscribe"]


class BatchInsertionFunction(Protocol):
    """Batch insertion function, returning event IDs of succesfully inserted event payloads"""

    async def __call__(
        self,
        conn: AsyncConnection,
        events: Sequence[Event],
        action: t_action_literal,
        /,
    ) -> list[int]: ...
