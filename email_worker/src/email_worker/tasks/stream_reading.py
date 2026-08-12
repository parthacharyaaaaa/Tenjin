import asyncio
from collections import defaultdict
from typing import Literal, Mapping

from redis import Redis
from resource_auxillary.events import StreamedEvent
from resource_auxillary.strings import EventName, StreamName

from email_worker.config.email_config import EmailConfig


async def stream_reader(
    config: EmailConfig,
    redis: Redis,
    stream_name: StreamName,
    dead_letter_queue: asyncio.Queue[StreamedEvent],
    group_name: str,
    consumer_name: str,
    requested_id: Literal[">"] | int = 0,
) -> list[StreamedEvent]:
    # result structure is actually:
    #                 event ID <-|            |-> payload
    # list[list[str, list[tuple[str, dict[str, str]]]]]
    #            |-> 0th element is stream name
    # Hinted as ResponseT btw, bravo
    result: list[list[list[tuple[str, dict[str, str]]]]] = await redis.xreadgroup(
        groupname=group_name,
        consumername=consumer_name,
        streams={stream_name.value: requested_id},
        count=config.WORKER.CONSUMER_READ_SIZE,
        noack=False,
        block=config.WORKER.CONSUMER_BLOCK_TIME,
    )

    if len(result[0][1]) == 0:
        return []

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
            await dead_letter_queue.put(
                StreamedEvent.safe_construct_from_malformed_stream(event_data)
            )
            continue

    return events


async def upstream_dispatcher(
    config: EmailConfig,
    redis: Redis,
    queue_mapping: Mapping[EventName, asyncio.Queue[tuple[StreamedEvent]]],
    dead_letter_queue: asyncio.Queue[StreamedEvent],
    stream_name: StreamName,
    group_name: str,
    consumer_name: str,
    read_history: bool = True,
) -> None:
    requested_id: Literal[">"] | int = 0 if read_history else ">"
    while True:
        events: list[StreamedEvent] = await stream_reader(
            config,
            redis,
            stream_name,
            dead_letter_queue,
            group_name,
            consumer_name,
            read_history,
        )

        if not events and requested_id == requested_id == 0:
            requested_id = ">"

        event_mapping: defaultdict[
            asyncio.Queue[tuple[StreamedEvent, ...]], list[StreamedEvent]
        ] = defaultdict(list)
        for event in events:
            event_mapping[queue_mapping[event.name]].append(event)
        for consumer_queue, events_batch in event_mapping.items():
            await consumer_queue.put(tuple(events_batch))

        await asyncio.sleep(config.WORKER.CONSUMER_READ_INTERVAL)
