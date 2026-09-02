from typing import TypeVar
from resource_auxillary.events import StreamedEvent
from datetime import timedelta
from typing import Protocol
from dataclasses import dataclass
from collections.abc import Iterable, Sequence

from redis.asyncio import Redis

from auxillary.utils import cache_repr

from resource_auxillary.events import Event
from resource_auxillary.strings import StreamName
from resource_auxillary.typing import HasEventID

T = TypeVar("T", bound=HasEventID)


class EventStreamManager(Protocol):

    @staticmethod
    def timedelta_to_broker_units(t: timedelta) -> int: ...

    async def read_events(
        self,
        stream: StreamName,
        consumer_group: str,
        consumer: str,
        offset: int | str,
        batch_size: int,
        timeout: timedelta | None,
    ) -> tuple[list[StreamedEvent], list[StreamedEvent]]: ...

    async def acknowledge_events(
        self,
        events: Iterable[HasEventID],
        event_stream_name: StreamName,
        group_name: str,
    ) -> None: ...

    async def stream_events(
        self, events: Iterable[Event], stream_name: StreamName
    ) -> None: ...

    async def amortize_events(
        self,
        events: Iterable[HasEventID],
        event_stream_name: StreamName,
        group_name: str,
        dlq_stream_name: StreamName,
    ) -> None: ...

    async def trim_duplicate_events(
        self,
        batch: list[T],
        fresh_event_ids: Sequence[int],
        stream_name: StreamName,
        group_name: str,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class RedisStreamManager:
    """Event-stream manager for Redis streams"""

    redis_client: Redis

    @staticmethod
    def timedelta_to_broker_units(t: timedelta) -> int:
        return int(t.total_seconds()) * 1000

    async def read_events(
        self,
        stream: StreamName,
        consumer_group: str,
        consumer: str,
        offset: int | str,
        batch_size: int,
        timeout: timedelta | None,
    ) -> tuple[list[StreamedEvent], list[StreamedEvent]]:
        result: list[list[list[tuple[str, dict[str, str]]]]] = (
            await self.redis_client.xreadgroup(
                groupname=consumer_group,
                consumername=consumer,
                streams={stream.value: offset},
                count=batch_size,
                noack=False,
                block=(
                    timeout
                    if timeout is None
                    else self.timedelta_to_broker_units(timeout)
                ),
            )
        )
        malformed_events: list[StreamedEvent] = []

        if len(result[0][1]) == 0:
            return [], malformed_events

        event_stream_subset = result[0][1]
        del result

        events: list[StreamedEvent] = []
        for event_data in event_stream_subset:
            try:
                event: StreamedEvent = StreamedEvent.construct_from_stream_record(
                    event_data
                )
                events.append(event)
            except ValueError:
                malformed_events.append(
                    StreamedEvent.safe_construct_from_malformed_stream(event_data)
                )
                continue

        return events, malformed_events

    async def acknowledge_events(
        self,
        events: Iterable[HasEventID],
        event_stream_name: StreamName,
        group_name: str,
    ) -> None:
        self.redis_client
        async with self.redis_client.pipeline(transaction=True) as pipeline:
            for event in events:
                pipeline.xack(event_stream_name, group_name, event.event_id)
            await pipeline.execute()

    async def stream_events(
        self, events: Iterable[Event], stream_name: StreamName
    ) -> None:
        async with self.redis_client.pipeline(transaction=True) as pipeline:
            for event in events:
                await pipeline.xadd(stream_name, cache_repr(event))
            await pipeline.execute()

    async def amortize_events(
        self,
        events: Iterable[StreamedEvent],
        event_stream_name: StreamName,
        group_name: str,
        dlq_stream_name: StreamName,
    ) -> None:
        async with self.redis_client.pipeline(transaction=True) as pipeline:
            for event in events:
                pipeline.xack(event_stream_name, group_name, event.event_id)
                pipeline.xadd(dlq_stream_name, cache_repr(event), id=event.event_id)
            await pipeline.execute()

    async def trim_duplicate_events(
        self,
        batch: list[T],
        fresh_event_ids: Sequence[int],
        stream_name: StreamName,
        group_name: str,
    ) -> None:
        async with self.redis_client.pipeline() as pipeline:
            for event in batch.copy():
                if event.event_id not in fresh_event_ids:
                    batch.remove(event)
                    pipeline.xack(stream_name, group_name, event.event_id)
            await pipeline.execute()
