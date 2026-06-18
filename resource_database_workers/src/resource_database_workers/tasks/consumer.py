import asyncio
from collections import defaultdict

from redis.asyncio import Redis

from resource_database_workers.config.config import AppConfig
from resource_database_workers.datastructures.queues import QueueRegistry
from resource_auxillary.strings import EventName, StreamName
from resource_auxillary.events import Event


async def consume_from_stream(
    config: AppConfig,
    redis: Redis,
    queue_registry: QueueRegistry,
    group_name: str,
    consumer_name: str,
    read_history: bool = True,
) -> None:
    requested_id: str | int = 0 if read_history else ">"
    while True:
        # result structure is actually:
        #                 event ID <-|            |-> payload
        # list[list[str, list[tuple[str, dict[str, str]]]]]
        #            |-> 0th element is stream name
        # Hinted as ResponseT btw, bravo
        result: list[list[list[tuple[str, dict[str, str]]]]] = await redis.xreadgroup(
            groupname=group_name,
            consumername=consumer_name,
            streams={StreamName.USER_INTERACTIONS: requested_id},
            count=config.WORKER.CONSUMER_READ_SIZE,
            noack=False,
            block=config.WORKER.CONSUMER_BLOCK_TIME,
        )

        if len(result[0][1]) == 0:
            if requested_id == 0:  # History cleared
                requested_id = ">"
            await asyncio.sleep(config.WORKER.CONSUMER_READ_INTERVAL)
            continue

        event_stream_subset = result[0][1]
        del result

        event_dict: defaultdict[asyncio.Queue[tuple[Event, ...]], list[Event]] = (
            defaultdict(list[Event])
        )
        for event_data in event_stream_subset:
            try:
                event_name: EventName = EventName(event_data[1]["name"])
                event: Event = Event.serialize_from_stream(event[1])  # type: ignore
            except ValueError:
                ...  # DLQ
                continue
            event_dict[queue_registry.event_queue_mapping[event_name]].append(event)

        for queue, events in event_dict.items():
            queue.put_nowait(tuple(events))

        await asyncio.sleep(config.WORKER.CONSUMER_READ_INTERVAL)
