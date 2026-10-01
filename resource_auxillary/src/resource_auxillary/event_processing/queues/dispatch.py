import asyncio
from collections import defaultdict
from collections.abc import Sequence
from typing import Any, Literal

from resource_auxillary.config_mixins.worker_consumer import SupportsWorkerStreamReading
from resource_auxillary.event_processing.event_stream_manager import EventStreamManager
from resource_auxillary.event_processing.queues.registry import EventQueueRegistry
from resource_auxillary.events import StreamedEvent
from resource_auxillary.strings import EventName, StreamName


async def _event_queue_populate(
    events: Sequence[StreamedEvent],
    event_queue_registry: EventQueueRegistry[Any],
    *,
    batched: bool = False,
) -> None:
    if batched:
        events_batch: defaultdict[EventName, list[StreamedEvent]] = defaultdict(list)
        for event in events:
            events_batch[event.name].append(event)
        for event_name, batch in events_batch.items():
            await event_queue_registry.append_to_queue(
                event_name, tuple(batch), make_queue=True
            )
    else:
        for event in events:
            await event_queue_registry.append_to_queue(
                event.name, event, make_queue=True
            )


async def base_dispatcher(
    config: SupportsWorkerStreamReading,
    event_stream_manager: EventStreamManager,
    queue_registry: EventQueueRegistry[Any],
    dlq_stream_name: StreamName,
    stream_name: StreamName,
    group_name: str,
    consumer_name: str,
    read_history: bool = True,
) -> None:
    requested_id: Literal[">"] | int = 0 if read_history else ">"
    while True:
        events, malformed_events = await event_stream_manager.read_events(
            stream_name,
            group_name,
            consumer_name,
            requested_id,
            config.CONSUMER_READ_SIZE,
            config.CONSUMER_BLOCK_TIME,
        )

        if malformed_events:
            await event_stream_manager.amortize_events(
                malformed_events, stream_name, group_name, dlq_stream_name
            )

        if not events and requested_id == 0:
            requested_id = ">"
            continue

        await _event_queue_populate(events, queue_registry)
        await asyncio.sleep(config.CONSUMER_READ_INTERVAL.total_seconds())
