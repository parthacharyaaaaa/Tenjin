import asyncio
from dataclasses import dataclass, field
from functools import cached_property
from types import MappingProxyType

from auxillary.singleton import SingletonMetaclass
from resource_auxillary.events import StreamedEvent
from resource_auxillary.strings import EventName


@dataclass(slots=True, frozen=True)
class QueueRegistry(metaclass=SingletonMetaclass):
    GENERAL_QUEUE: asyncio.Queue[tuple[StreamedEvent]] = field(
        default_factory=asyncio.Queue
    )
    PASSWORD_RECOVERY_QUEUE: asyncio.Queue[tuple[StreamedEvent]] = field(
        default_factory=asyncio.Queue
    )

    @cached_property
    def event_queue_mapping(self) -> MappingProxyType[EventName, asyncio.Queue]:
        return MappingProxyType(
            {
                EventName.USER_DELETION_EMAIL: self.GENERAL_QUEUE,
                EventName.USER_REGISTRATION_EMAIL: self.GENERAL_QUEUE,
                EventName.USER_PASSWORD_RECOVERY_EMAIL: self.PASSWORD_RECOVERY_QUEUE,
            }
        )
