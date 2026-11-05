from dataclasses import dataclass
from typing import Final, Literal, overload

from auxillary.data_structures.repository import AbstractWorkRepository
from sqlalchemy.sql.expression import insert

from auth_server.models.database import EventDelivery, InboxTable
from auth_server.models.events.downstream import EVENT_DOWNSTREAM_MAPPING
from auth_server.models.events.events import InboxEvent


@dataclass(slots=True)
class InboxRepository(AbstractWorkRepository):
    @overload
    async def insert_into_inbox(
        self, event: InboxEvent, *, return_message_id: Literal[False] = False
    ) -> None: ...
    @overload
    async def insert_into_inbox(
        self, event: InboxEvent, *, return_message_id: Literal[True]
    ) -> int: ...

    async def insert_into_inbox(
        self, event: InboxEvent, *, return_message_id: bool = False
    ) -> int | None:
        async with self._work_scoped_session() as session:
            message_id: Final[int] = (
                await session.execute(
                    insert(InboxTable)
                    .values(event_type=event.type, payload=event.payload)
                    .returning(InboxTable.message_id)
                )
            ).scalar_one()
            await session.execute(
                insert(EventDelivery),
                tuple(
                    {"message_id": message_id, "subscriber_type": sub_event}
                    for sub_event in EVENT_DOWNSTREAM_MAPPING[event.type]
                ),
            )
            if return_message_id:
                return message_id
