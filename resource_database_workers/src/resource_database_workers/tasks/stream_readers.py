from resource_database_workers.dependencies.annotations import (
    DEAD_LETTER_QUEUE_REGISTRY,
    STREAM_NAME,
)
from collections.abc import Sequence
from typing import Any
from resource_database_workers.datastructures.queues import EventQueueRegistry
import asyncio
from collections import defaultdict
from typing import Literal

from resource_auxillary.strings import EventName, StreamName
from resource_auxillary.events import StreamedEvent

from resource_database_workers.dependencies.annotations import (
    APP_CONFIG,
    DEAD_LETTER_STREAM_NAME,
    GROUP_NAME,
    CONSUMER_ID,
    UPSTREAM_QUEUE_REGISTRY,
    DOWNSTREAM_QUEUE_REGISTRY,
    EVENT_STREAM_MANAGER,
)


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
    config: APP_CONFIG,
    event_stream_manager: EVENT_STREAM_MANAGER,
    queue_registry: EventQueueRegistry[Any],
    dlq_stream_name: DEAD_LETTER_STREAM_NAME,
    stream_name: StreamName,
    group_name: GROUP_NAME,
    consumer_name: CONSUMER_ID,
    read_history: bool = True,
) -> None:
    requested_id: Literal[">"] | int = 0 if read_history else ">"
    while True:
        events, malformed_events = await event_stream_manager.read_events(
            stream_name,
            group_name,
            consumer_name,
            requested_id,
            config.WORKER.CONSUMER_READ_SIZE,
            config.WORKER.CONSUMER_BLOCK_TIME,
        )

        if malformed_events:
            await event_stream_manager.amortize_events(
                malformed_events, stream_name, group_name, dlq_stream_name
            )

        if not events and requested_id == 0:
            requested_id = ">"
            continue

        await _event_queue_populate(events, queue_registry)
        await asyncio.sleep(config.WORKER.CONSUMER_READ_INTERVAL)


async def upstream_dispatcher(
    config: APP_CONFIG,
    event_stream_manager: EVENT_STREAM_MANAGER,
    queue_registry: UPSTREAM_QUEUE_REGISTRY,
    dlq_stream_name: DEAD_LETTER_STREAM_NAME,
    stream_name: STREAM_NAME,
    group_name: GROUP_NAME,
    consumer_name: CONSUMER_ID,
    read_history: bool = True,
) -> None:
    await base_dispatcher(
        config,
        event_stream_manager,
        queue_registry,
        dlq_stream_name,
        stream_name,
        group_name,
        consumer_name,
        read_history,
    )


async def downstream_dispatcher(
    config: APP_CONFIG,
    event_stream_manager: EVENT_STREAM_MANAGER,
    queue_registry: DOWNSTREAM_QUEUE_REGISTRY,
    dlq_stream_name: DEAD_LETTER_STREAM_NAME,
    stream_name: STREAM_NAME,
    group_name: GROUP_NAME,
    consumer_name: CONSUMER_ID,
    read_history: bool = True,
) -> None:
    await base_dispatcher(
        config,
        event_stream_manager,
        queue_registry,
        dlq_stream_name,
        stream_name,
        group_name,
        consumer_name,
        read_history,
    )


async def dlq_dispatcher(
    config: APP_CONFIG,
    event_stream_manager: EVENT_STREAM_MANAGER,
    queue_registry: DEAD_LETTER_QUEUE_REGISTRY,
    dlq_stream_name: DEAD_LETTER_STREAM_NAME,
    stream_name: STREAM_NAME,
    group_name: GROUP_NAME,
    consumer_name: CONSUMER_ID,
    read_history: bool = True,
) -> None:
    await base_dispatcher(
        config,
        event_stream_manager,
        queue_registry,
        dlq_stream_name,
        stream_name,
        group_name,
        consumer_name,
        read_history,
    )
