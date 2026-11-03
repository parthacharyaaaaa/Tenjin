from dataclasses import dataclass
from typing import Any, Literal, overload

from auxillary.data_structures.repository import AbstractWorkRepository
from sqlalchemy.sql.expression import insert

from auth_server.models.data_transport.inbox import InboxEventResult
from auth_server.models.database import InboxTable
from auth_server.models.events.events import InboxEvent


@dataclass(slots=True)
class InboxRepository(AbstractWorkRepository):
    @overload
    async def insert_into_inbox(
        self, event: InboxEvent, *, returning: Literal[False] = False
    ) -> None: ...
    @overload
    async def insert_into_inbox(
        self, event: InboxEvent, *, returning: Literal[True] = True
    ) -> InboxEventResult: ...

    async def insert_into_inbox(
        self, event: InboxEvent, *, returning: bool = False
    ) -> InboxEventResult | None:
        values: dict[str, Any] = {"event_type": event.type, "payload": event.payload}
        async with self._work_scoped_session() as session:
            if not returning:
                await session.execute(
                    insert(InboxTable).values(values).returning(InboxTable)
                )
            inbox_result: InboxTable = (
                await session.execute(
                    insert(InboxTable).values(values).returning(InboxTable)
                )
            ).scalar_one()
            return InboxEventResult.construct_from_orm(inbox_result)
