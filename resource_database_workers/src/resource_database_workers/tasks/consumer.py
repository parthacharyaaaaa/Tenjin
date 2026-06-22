import asyncio
from collections import defaultdict
from datetime import datetime
import time
from typing import Final

from psycopg.errors import OperationalError, InternalError
from psycopg_pool import AsyncConnectionPool
from psycopg.sql import Composed
from resource_auxillary.cache import derive_cache_key

from redis.asyncio import Redis

from auxillary.utils import cache_repr, json_repr
from resource_auxillary.database import ORPHAN_MAPPING, StrongEntity

from resource_database_workers.config.config import AppConfig
from resource_database_workers.datastructures.queues import QueueRegistry
from resource_auxillary.strings import NAME_SEPERATOR, EventName, StreamName
from resource_auxillary.events import Event

from resource_database_workers.datastructures.dead_counter_batch import DeadCounterBatch
from resource_database_workers.tasks.selections import select_author_deltas
from resource_database_workers.utils.typing import (
    BatchDeletionFunction,
    BatchInsertionFunction,
    t_action_literal,
)
from resource_database_workers.utils.sql_templates import (
    format_dlq_insertion_sql,
    format_counters_dlq_insertion_sql,
    prepare_deltas_selection,
)


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
            except (KeyError, ValueError):
                await queue_registry.dead_letter.put(
                    Event.safe_construct_from_malformed_stream(event_data[1])
                )
                continue
            event_dict[queue_registry.event_queue_mapping[event_name]].append(event)

        for queue, events in event_dict.items():
            queue.put_nowait(tuple(events))

        await asyncio.sleep(config.WORKER.CONSUMER_READ_INTERVAL)


async def queue_insertion_consumer(
    config: AppConfig,
    pool: AsyncConnectionPool,
    redis: Redis,
    queue: asyncio.Queue[tuple[Event]],
    dead_letter_queue: asyncio.Queue[Event],
    batch_function: BatchInsertionFunction,
    stream_name: StreamName,
    group_name: str,
    action: t_action_literal,
) -> None:
    batch: list[Event] = []
    successful_events: list[int] = []
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

        failed: bool = False
        async with pool.connection() as conn:
            for attempt in range(config.WORKER.MAX_RETRIES):
                try:
                    # TODO: Add logic to insert into dedup table
                    inserted_ids: list[int] = await batch_function(conn, batch, action)
                    await conn.commit()

                    successful_events.extend(inserted_ids)
                    del inserted_ids
                    failed = False
                    break
                except (OperationalError, InternalError):
                    failed = True
                    await conn.rollback()
                except Exception:
                    # Ideally a subclass of psycopg.errors.Error,
                    # but Python errors are also non-transient
                    failed_events: list[Event] = [
                        event
                        for event in batch
                        if event.event_id not in successful_events
                    ]
                    for event in failed_events:
                        await dead_letter_queue.put(event)
                    await redis.xack(
                        stream_name, group_name, *(e.event_id for e in failed_events)
                    )
                    failed_events.clear()
                    await conn.rollback()
                    break

            await redis.xack(
                StreamName.USER_INTERACTIONS,
                config.WORKER.CONSUMER_GROUP_NAME,
                *successful_events,
            )
            successful_events.clear()

            if failed:
                for event in batch:
                    await dead_letter_queue.put(event)
                await redis.xack(stream_name, group_name, *(e.event_id for e in batch))

            batch.clear()
            reference_time = time.monotonic()


async def queue_downstream_deletion_consumer(
    config: AppConfig,
    pool: AsyncConnectionPool,
    redis: Redis,
    queue: asyncio.Queue[Event],
    dead_letter_queue: asyncio.Queue[Event],
    batch_function: BatchDeletionFunction,
    stream_name: StreamName,
    group_name: str,
) -> None:
    while True:
        event: Event = await queue.get()
        try:
            foreign_key: int = int(event.payload["foreign_key"])
            if "deleted_at" in event.payload:
                deletion_time = datetime.fromisoformat(event.payload["deleted_at"])
            else:
                deletion_time = datetime.fromtimestamp(event.created_at)
        except (KeyError, ValueError):
            await dead_letter_queue.put(event)
            continue

        if event.name == EventName.ORPHANED_POST_DELETE:
            parent_table = StrongEntity.POST
        elif event.name == EventName.ORPHANED_COMMENT_DELETE:
            parent_table = StrongEntity.COMMENT
        else:
            await dead_letter_queue.put(event)
            continue

        orphan_table, foreign_key_column = ORPHAN_MAPPING[parent_table][1:]

        failed: bool = False
        async with pool.connection() as conn:
            for attempt in range(config.WORKER.MAX_RETRIES):
                try:
                    # TODO: Add logic to insert into dedup table
                    await batch_function(
                        conn,
                        foreign_key,
                        orphan_table,
                        foreign_key_column,
                        deletion_time,
                    )
                    await conn.commit()
                    failed = False
                    break
                except (OperationalError, InternalError):
                    failed = True
                    await conn.rollback()
                except Exception:
                    # Ideally a subclass of psycopg.errors.Error,
                    # but Python errors are also non-transient
                    await dead_letter_queue.put(event)
                    await redis.xack(stream_name, group_name, event.event_id)
                    await conn.rollback()
                    break

            if failed:  # Non-transient faults exceed max retries
                await dead_letter_queue.put(event)
                await redis.xack(stream_name, group_name, event.event_id)
            else:  # downstream deletion succesful
                await redis.xack(
                    stream_name,
                    group_name,
                    event.event_id,
                )
                event_name: EventName = (
                    EventName.DOWNSTREAM_POST_DECREMENT
                    if parent_table == StrongEntity.FORUM
                    else EventName.DOWNSTREAM_COMMENT_DECREMENT
                )
                event: Event = Event(
                    name=event_name, payload={"deletion_time": deletion_time}
                )  # type: ignore
                await redis.xadd(
                    name=StreamName.DOWNSTREAM_DELETIONS, fields=cache_repr(event)
                )


async def queue_downstream_decrement_consumer(
    config: AppConfig,
    pool: AsyncConnectionPool,
    redis: Redis,
    queue: asyncio.Queue[Event],
    dead_letter_queue: asyncio.Queue[Event],
    stream_name: StreamName,
    group_name: str,
) -> None:
    while True:
        event: Event = await queue.get()
        try:
            deletion_time = datetime.fromisoformat(event.payload["deletion_time"])
            if event.name == EventName.DOWNSTREAM_POST_DECREMENT:
                parent_entity = StrongEntity.FORUM
                author_column = "author_id"
                hashmap_name = NAME_SEPERATOR.join(
                    (StrongEntity.USER, StrongEntity.POST)
                )
            elif event.name == EventName.DOWNSTREAM_COMMENT_DECREMENT:
                parent_entity = StrongEntity.POST
                author_column = "author_id"
                hashmap_name = NAME_SEPERATOR.join(
                    (StrongEntity.USER, StrongEntity.COMMENT)
                )
            else:
                raise ValueError()

            identifier_column, table = ORPHAN_MAPPING[parent_entity][:2]
        except (KeyError, ValueError):
            await dead_letter_queue.put(event)
            continue

        failed: bool = False
        limit, offset = config.WORKER.DOWNSTREAM_COUNTER_BATCH_SIZE, 0
        async with pool.connection() as conn:
            for attempt in range(config.WORKER.MAX_RETRIES):
                try:
                    results: list[dict[str, int]] = [{}]
                    while results:
                        results = await select_author_deltas(
                            conn,
                            deletion_time,
                            limit,
                            offset,
                            author_column,
                            table,
                            identifier_column,
                        )
                        offset = offset + limit
                        async with redis.pipeline() as pipeline:
                            for result in results:
                                pipeline.hincrby(
                                    hashmap_name,
                                    derive_cache_key(
                                        StrongEntity.USER.value, result["author_id"]
                                    ),
                                    -result["delta"],
                                )
                            await pipeline.execute()
                except (OperationalError, InternalError):
                    failed = True
                    await conn.rollback()
                    continue
                except Exception:
                    await dead_letter_queue.put(event)
                    await redis.xack(stream_name, group_name, event.event_id)
                    await conn.rollback()
                    break

            if failed:  # Non-transient faults exceed max retries
                await dead_letter_queue.put(event)
                await redis.xack(stream_name, group_name, event.event_id)
            else:  # downstream deletion succesful
                await redis.xack(
                    stream_name,
                    group_name,
                    event.event_id,
                )


async def dlq_consumer(pool: AsyncConnectionPool, queue: asyncio.Queue[Event]) -> None:
    insertion_sql: Final[Composed] = format_dlq_insertion_sql()
    while True:
        dlq_event: Event = await queue.get()
        async with pool.connection() as conn:
            await conn.execute(
                insertion_sql, (dlq_event.event_id, json_repr(dlq_event))
            )
            await conn.commit()


async def counters_dlq_consumer(
    pool: AsyncConnectionPool, queue: asyncio.Queue[DeadCounterBatch]
) -> None:
    insertion_sql: Final[Composed] = format_counters_dlq_insertion_sql()
    while True:
        batch: DeadCounterBatch = await queue.get()
        async with pool.connection() as conn:
            await conn.execute(
                insertion_sql,
                (batch.table, batch.column, batch.failure_time, batch.counters),
            )
            await conn.commit()
