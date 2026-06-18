import asyncio
from collections import defaultdict
import time

from psycopg.errors import LockNotAvailable, OperationalError, InternalError, Error
from psycopg_pool import AsyncConnectionPool

from redis.asyncio import Redis

from resource_database_workers.config.config import AppConfig
from resource_database_workers.datastructures.queues import QueueRegistry
from resource_auxillary.strings import EventName, StreamName
from resource_auxillary.events import Event

from resource_database_workers.utils.typing import BatchInsertionFunction


async def stream_consumer(
    config: AppConfig,
    redis: Redis,
    queue_registry: QueueRegistry,
    stream_name: StreamName,
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
            streams={stream_name.value: requested_id},
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


async def queue_consumer(
    config: AppConfig,
    pool: AsyncConnectionPool,
    redis: Redis,
    queue: asyncio.Queue[tuple[Event]],
    batch_function: BatchInsertionFunction,
) -> None:
    batch: list[Event] = []
    successful_events: list[str] = []
    reference_time: float = time.monotonic()
    while True:
        if not (
            (len(batch) >= config.WORKER.IQ_CONSUMER_BATCH_SIZE_QUOTA)
            or time.monotonic() - reference_time
            > config.WORKER.IQ_CONSUMER_BASE_WAITING_TIME
        ):
            try:
                new_entries: tuple[Event] = await asyncio.wait_for(
                    queue.get(), config.WORKER.IQ_CONSUMER_GET_TIMEOUT
                )
                if not batch:
                    reference_time = time.monotonic()
                batch.extend(new_entries)
            except asyncio.TimeoutError:
                await asyncio.sleep(config.WORKER.IQ_CONSUMER_SLEEP_INTERVAL)
            continue

        if not batch:
            await asyncio.sleep(config.WORKER.IQ_CONSUMER_SLEEP_INTERVAL)
            reference_time = time.monotonic()
            continue

        async with pool.connection() as conn:
            try:
                # Logic to insert into dedup table, using CTE or exectemany and view to fetch unique events
                inserted_ids: list[str] = await batch_function(conn, batch)
                await conn.commit()
                successful_events.extend(inserted_ids)
                del inserted_ids
            except (LockNotAvailable, OperationalError, InternalError):
                # Possible recoverable database exceptions
                ...
            except Error:
                # Unrecoverable, straight to DLQ
                ...
            finally:
                await redis.xack(
                    StreamName.USER_INTERACTIONS,
                    config.WORKER.CONSUMER_GROUP_NAME,
                    *successful_events,
                )
                successful_events.clear()
                batch.clear()
                reference_time = time.monotonic()
