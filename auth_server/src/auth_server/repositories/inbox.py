from dataclasses import dataclass
from typing import Final, Literal, overload

from auxillary.data_structures.repository import AbstractWorkRepository
from sqlalchemy.ext.asyncio.session import AsyncSession
from sqlalchemy.sql.expression import insert, text

from auth_server.datastructures.exceptions.database import (
    FailedApplicationSerializationError,
)
from auth_server.models.advisory_locks import LockNamespace
from auth_server.models.database import EventDelivery, InboxTable
from auth_server.models.events.downstream import EVENT_DOWNSTREAM_MAPPING
from auth_server.models.events.events import InboxEvent


@dataclass(slots=True)
class InboxRepository(AbstractWorkRepository):
    @overload
    async def insert_into_inbox(
        self,
        event: InboxEvent,
        *,
        return_message_id: Literal[False] = False,
        serialization_lock: int | None = None,
    ) -> None: ...
    @overload
    async def insert_into_inbox(
        self,
        event: InboxEvent,
        *,
        return_message_id: Literal[True],
        serialization_lock: int | None = None,
    ) -> int: ...

    async def set_transactional_advisory_exclusive_lock(
        self, session: AsyncSession, namespace: int, lock: int
    ) -> bool:
        return await session.scalar(
            text(
                "SELECT pg_try_advisory_xact_lock(:namespace, :lock_value) AS lock_set;"
            ),
            {"namespace": namespace, "lock": lock},
        )

    async def insert_into_inbox(
        self,
        event: InboxEvent,
        *,
        return_message_id: bool = False,
        serialization_lock: int | None = None,
    ) -> int | None:
        async with self._work_scoped_session() as session:
            if serialization_lock is not None and not (
                await self.set_transactional_advisory_exclusive_lock(
                    session, LockNamespace.EVENT_SERIALIZATION, serialization_lock
                )
            ):
                raise FailedApplicationSerializationError(
                    f"Failed to acquire serialization lock Value: {serialization_lock}"
                )

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
