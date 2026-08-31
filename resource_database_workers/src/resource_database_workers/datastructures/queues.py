from typing import Literal
from typing import overload
from typing import TypeVar
from typing import Generic
import asyncio
from dataclasses import dataclass, field
from types import MappingProxyType

from auxillary.singleton import SingletonMetaclass

from resource_auxillary.events import StreamedEvent
from resource_auxillary.strings import EventName

T = TypeVar("T")


@dataclass(slots=True, frozen=True)
class EventQueueRegistry(Generic[T]):
    # _mutex: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _event_queue_mapping: dict[EventName, asyncio.Queue[T]] = field(
        default_factory=dict
    )

    def register_event_queue(self, event: EventName, *, exist_ok: bool = True) -> None:
        event_queue: asyncio.Queue[T] | None = self._event_queue_mapping.get(event)
        if event_queue:
            if exist_ok:
                return
            raise ValueError(f"Queue for event '{event}' already exists")

        self._event_queue_mapping[event] = asyncio.Queue()

    def remove_event_queue(self, event: EventName, *, missing_ok: bool = True) -> None:
        event_queue: asyncio.Queue[T] | None = self._event_queue_mapping.get(event)
        if not event_queue:
            if missing_ok:
                return
            raise KeyError(f"No queue for event '{event}' found")

        del self._event_queue_mapping[event]

    async def append_to_queue(
        self, event: EventName, entry: T, *, make_queue: bool = True
    ) -> None:
        event_queue: asyncio.Queue[T] | None = self._event_queue_mapping.get(event)
        if not event_queue:
            if make_queue:
                event_queue = asyncio.Queue()
                self._event_queue_mapping[event] = event_queue
            else:
                raise KeyError(f"No queue for event '{event}' found")
        await event_queue.put(entry)

    async def pop_from_queue(self, event: EventName) -> T:
        event_queue: asyncio.Queue[T] | None = self._event_queue_mapping.get(event)
        if not event_queue:
            raise KeyError(f"No queue for event '{event}' found")
        return await event_queue.get()

    @overload
    def get_event_queue(
        self, event: EventName, *, raise_on_miss: Literal[True] = True
    ) -> asyncio.Queue[T]: ...
    @overload
    def get_event_queue(
        self, event: EventName, *, raise_on_miss: Literal[False] = False
    ) -> asyncio.Queue[T] | None: ...

    def get_event_queue(
        self, event: EventName, *, raise_on_miss: bool = True
    ) -> asyncio.Queue[T] | None:
        event_queue: asyncio.Queue[T] | None = self._event_queue_mapping.get(event)
        if not event_queue and raise_on_miss:
            raise KeyError(f"No queue for event '{event}' found")
        return event_queue

    @property
    def mapping_view(self) -> MappingProxyType[EventName, asyncio.Queue[T]]:
        return MappingProxyType(self._event_queue_mapping)


@dataclass(slots=True, frozen=True)
class EventQueueRegistryContainer(metaclass=SingletonMetaclass):
    upstream_registry: EventQueueRegistry[asyncio.Queue[tuple[StreamedEvent, ...]]] = (
        field(default_factory=EventQueueRegistry)
    )
    downstream_registry: EventQueueRegistry[asyncio.Queue[StreamedEvent]] = field(
        default_factory=EventQueueRegistry
    )
