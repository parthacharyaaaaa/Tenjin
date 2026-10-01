import asyncio
from dataclasses import dataclass, field
from typing import TypeVar

from auxillary.singleton import SingletonMetaclass
from resource_auxillary.event_processing.queues.registry import (
    EventQueueRegistry,
    TieredQueueRegistry,
)
from resource_auxillary.events import StreamedEvent
from resource_auxillary.strings import EventName

T = TypeVar("T")


@dataclass(slots=True, frozen=True, weakref_slot=True)
class EventQueueRegistryContainer(metaclass=SingletonMetaclass):
    upstream_registry: EventQueueRegistry[asyncio.Queue[tuple[StreamedEvent, ...]]] = (
        field(default_factory=EventQueueRegistry)
    )

    dlq_registry: EventQueueRegistry[asyncio.Queue[StreamedEvent]] = field(
        default_factory=lambda: TieredQueueRegistry(
            default_event=EventName.DEAD_LETTER_SENTINEL
        )
    )
