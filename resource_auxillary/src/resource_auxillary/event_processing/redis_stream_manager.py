from dataclasses import dataclass
from collections.abc import Iterable, Sequence

from redis.asyncio import Redis

from auxillary.utils import cache_repr

from resource_auxillary.events import Event, StreamedEvent
from resource_auxillary.strings import StreamName
from resource_auxillary.typing import HasEventID


@dataclass(frozen=True, slots=True)
class RedisStreamManager:
    """Event-stream manager for Redis streams"""

    redis_client: Redis

    async def acknowledge_event(
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

    async def amortize_event(
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
        batch: list[HasEventID],
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
