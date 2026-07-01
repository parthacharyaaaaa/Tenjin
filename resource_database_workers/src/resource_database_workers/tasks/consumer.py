import asyncio
from datetime import datetime
import time
from typing import Final, Generator, Iterable

from psycopg.errors import OperationalError, InternalError
from psycopg_pool import AsyncConnectionPool
from psycopg.sql import Composed

from redis.asyncio import Redis

from auxillary.utils import json_repr
from resource_auxillary.datastructures.database import StrongEntity

from resource_database_workers.config.config import AppConfig
from resource_auxillary.strings import StreamName
from resource_auxillary.events import StreamedEvent

from resource_database_workers.datastructures.dead_counter_batch import DeadCounterBatch
from resource_database_workers.utils.coordination import (
    batch_dedup_insert_events,
    dedup_insert_event,
)
from resource_database_workers.utils.tasks import (
    dispatch_downstream_counter_decrements,
    dispatch_downstream_events,
    emit_downstream_counter_decrement_updates,
)
from resource_database_workers.tasks.selections import select_decrement_deltas
from resource_database_workers.utils.typing import (
    BatchDeletionFunction,
    BatchDownstreamDeletionFunction,
    BatchInsertionFunction,
    t_action_literal,
)
from resource_database_workers.utils.sql_templates import (
    format_dlq_insertion_sql,
    format_counters_dlq_insertion_sql,
)
from resource_database_workers.datastructures.downstream import (
    DownstreamCounterDecrementData,
    DownstreamDeletionData,
    reconstruct_downstream_counter_data_from_stream,
    reconstruct_downstream_data_from_stream,
)


def _create_user_cleanup_generator(
    events: Iterable[StreamedEvent],
) -> Generator[tuple[int, datetime], None, None]:
    return (
        (event.payload["user_id"], event.payload["time_deleted"]) for event in events
    )


async def populate_events_batch_from_queue(
    config: AppConfig,
    queue: asyncio.Queue[tuple[StreamedEvent, ...]],
    reference_time: float,
    batch: list[StreamedEvent],
) -> None:
    while True:
        if not (
            (len(batch) >= config.WORKER.IQ_CONSUMER_BATCH_SIZE_QUOTA)
            or time.monotonic() - reference_time
            > config.WORKER.IQ_CONSUMER_BASE_WAITING_TIME
        ):
            try:
                new_entries: tuple[StreamedEvent, ...] = await asyncio.wait_for(
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


async def user_orphan_consumer(
    config: AppConfig,
    pool: AsyncConnectionPool,
    redis: Redis,
    queue: asyncio.Queue[tuple[StreamedEvent]],
    dead_letter_queue: asyncio.Queue[StreamedEvent],
    stream_name: StreamName,
    group_name: str,
) -> None:
    batch: list[StreamedEvent] = []
    reference_time: float = time.monotonic()

    while True:
        await populate_events_batch_from_queue(config, queue, reference_time, batch)
        failed: bool = False
        async with pool.connection() as conn:
            fresh_event_ids: tuple[int, ...] = await batch_dedup_insert_events(
                conn, (e.event_id for e in batch)
            )
            async with redis.pipeline() as pipeline:
                for event in batch.copy():
                    if event.event_id not in fresh_event_ids:
                        batch.remove(event)
                        pipeline.xack(stream_name, group_name, event.event_id)
                await pipeline.execute()

            for attempt in range(config.WORKER.MAX_RETRIES):
                try:
                    await dispatch_downstream_events(
                        redis, StrongEntity.USER, _create_user_cleanup_generator(batch)
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
                    await conn.rollback()
                    failed = True
                    break

            await redis.xack(
                stream_name,
                group_name,
                *(e.event_id for e in batch),
            )

            if failed:
                for event in batch:
                    await dead_letter_queue.put(event)
                await redis.xack(stream_name, group_name, *(e.event_id for e in batch))

            batch.clear()
            reference_time = time.monotonic()


async def queue_insertion_consumer(
    config: AppConfig,
    pool: AsyncConnectionPool,
    redis: Redis,
    queue: asyncio.Queue[tuple[StreamedEvent]],
    dead_letter_queue: asyncio.Queue[StreamedEvent],
    batch_function: BatchInsertionFunction,
    stream_name: StreamName,
    group_name: str,
    action: t_action_literal | None = None,
) -> None:
    batch: list[StreamedEvent] = []
    successful_events: list[int] = []
    reference_time: float = time.monotonic()
    while True:
        await populate_events_batch_from_queue(config, queue, reference_time, batch)
        failed: bool = False
        async with pool.connection() as conn:
            fresh_event_ids: tuple[int, ...] = await batch_dedup_insert_events(
                conn, (e.event_id for e in batch)
            )
            async with redis.pipeline() as pipeline:
                for event in batch.copy():
                    if event.event_id not in fresh_event_ids:
                        batch.remove(event)
                        pipeline.xack(stream_name, group_name, event.event_id)
                await pipeline.execute()
            for attempt in range(config.WORKER.MAX_RETRIES):
                try:
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
                    failed_events: list[StreamedEvent] = [
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
                stream_name,
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


async def queue_deletion_consumer(
    config: AppConfig,
    pool: AsyncConnectionPool,
    redis: Redis,
    table: StrongEntity,
    identifier_column: str,
    queue: asyncio.Queue[tuple[StreamedEvent]],
    dead_letter_queue: asyncio.Queue[StreamedEvent],
    batch_function: BatchDeletionFunction,
    stream_name: StreamName,
    group_name: str,
) -> None:
    batch: list[StreamedEvent] = []
    reference_time: float = time.monotonic()

    while True:
        await populate_events_batch_from_queue(config, queue, reference_time, batch)
        failed: bool = False
        deletion_data: Generator[tuple[int, datetime, int]] = (
            (
                event.payload[identifier_column],
                event.payload["deleted_at"],
                event.event_id,
            )
            for event in batch
        )
        async with pool.connection() as conn:
            fresh_event_ids: tuple[int, ...] = await batch_dedup_insert_events(
                conn, (e.event_id for e in batch)
            )
            async with redis.pipeline() as pipeline:
                for event in batch.copy():
                    if event.event_id not in fresh_event_ids:
                        batch.remove(event)
                        pipeline.xack(stream_name, group_name, event.event_id)
                await pipeline.execute()

            for attempt in range(config.WORKER.MAX_RETRIES):
                try:
                    # TODO: Add logic to insert into dedup table
                    await batch_function(
                        conn, table.value, identifier_column, deletion_data
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
                    for event in batch:
                        await dead_letter_queue.put(event)
                    await redis.xack(
                        stream_name, group_name, *(e.event_id for e in batch)
                    )
                    batch.clear()
                    await conn.rollback()
                    break

            if failed:
                for event in batch:
                    await dead_letter_queue.put(event)
                    await redis.xack(
                        stream_name, group_name, *(e.event_id for e in batch)
                    )
            elif batch:
                await redis.xack(stream_name, group_name, *(e.event_id for e in batch))

            await dispatch_downstream_events(
                redis,
                table,
                (
                    (event.payload[identifier_column], event.payload["deleted_at"])
                    for event in batch
                ),
            )

            batch.clear()
            reference_time = time.monotonic()


async def queue_downstream_deletion_consumer(
    config: AppConfig,
    pool: AsyncConnectionPool,
    redis: Redis,
    queue: asyncio.Queue[StreamedEvent],
    dead_letter_queue: asyncio.Queue[StreamedEvent],
    batch_function: BatchDownstreamDeletionFunction,
    stream_name: StreamName,
    group_name: str,
) -> None:
    while True:
        event: StreamedEvent = await queue.get()
        try:
            event_payload: DownstreamDeletionData = (
                reconstruct_downstream_data_from_stream(event.payload)
            )
        except (KeyError, ValueError):
            await dead_letter_queue.put(event)
            continue

        failed: bool = False
        async with pool.connection() as conn:
            if not await dedup_insert_event(conn, event.event_id):
                await redis.xack(stream_name, group_name, event.event_id)
                continue

            for attempt in range(config.WORKER.MAX_RETRIES):
                try:
                    await batch_function(
                        conn,
                        event_payload["foreign_key"],
                        event_payload["orphan_table"],
                        event_payload["foreign_key_column"],
                        event_payload["deleted_at"],
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
                await dispatch_downstream_counter_decrements(
                    redis, event_payload["orphan_table"], event.event_id
                )


async def queue_downstream_decrement_consumer(
    config: AppConfig,
    pool: AsyncConnectionPool,
    redis: Redis,
    queue: asyncio.Queue[StreamedEvent],
    dead_letter_queue: asyncio.Queue[StreamedEvent],
    stream_name: StreamName,
    group_name: str,
) -> None:
    while True:
        event: StreamedEvent = await queue.get()
        try:
            event_payload: DownstreamCounterDecrementData = (
                reconstruct_downstream_counter_data_from_stream(event.payload)
            )
        except (KeyError, ValueError):
            await dead_letter_queue.put(event)
            continue

        failed: bool = False
        # Downstream counters may be too big to materialize all at once
        limit, offset = config.WORKER.DOWNSTREAM_COUNTER_BATCH_SIZE, 0
        async with pool.connection() as conn:
            if not await dedup_insert_event(conn, event.event_id):
                await redis.xack(stream_name, group_name, event.event_id)
                continue

            for attempt in range(config.WORKER.MAX_RETRIES):
                try:
                    # temp truthy tuple to enter loop
                    results: list[tuple[str, int]] = [("", 0)]
                    while results:
                        results: list[tuple[str, int]] = await select_decrement_deltas(
                            conn,
                            event_payload["affected_column_name"],
                            limit,
                            offset,
                            event_payload["affected_table_name"],
                            event_payload["deletion_author_event_id"],
                        )
                        offset += limit
                        await emit_downstream_counter_decrement_updates(
                            redis,
                            results,
                            event_payload["hashmap_name"],
                            event_payload["affected_table_name"],
                        )
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
            else:
                await redis.xack(
                    stream_name,
                    group_name,
                    event.event_id,
                )


async def dlq_consumer(
    pool: AsyncConnectionPool, queue: asyncio.Queue[StreamedEvent]
) -> None:
    insertion_sql: Final[Composed] = format_dlq_insertion_sql()
    while True:
        dlq_event: StreamedEvent = await queue.get()
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
