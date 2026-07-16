import asyncio
from datetime import datetime
import time
from typing import Generator

from psycopg.errors import OperationalError, InternalError
from psycopg_pool import AsyncConnectionPool

from redis.asyncio import Redis

from resource_auxillary.datastructures.database import StrongEntity

from resource_database_workers.config.config import AppConfig
from resource_auxillary.strings import StreamName
from resource_auxillary.events import StreamedEvent

from resource_database_workers.src.resource_database_workers.utils.worker_db import (
    retried_event_database_processing,
)
from resource_database_workers.utils.coordination import (
    atomic_emit_side_effects,
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
from resource_database_workers.datastructures.downstream import (
    DownstreamCounterDecrementData,
    DownstreamDeletionData,
    reconstruct_downstream_counter_data_from_stream,
    reconstruct_downstream_data_from_stream,
)
from resource_database_workers.utils.worker_redis import (
    ack_with_retries,
    declare_dead_with_retries,
    emit_side_effects_with_retries,
    populate_events_batch_from_queue,
    trim_duplicate_events,
)


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
                        redis,
                        StrongEntity.USER,
                        (
                            (event.payload["user_id"], event.payload["time_deleted"])
                            for event in batch
                        ),
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
    batch_function: BatchInsertionFunction,
    stream_name: StreamName,
    group_name: str,
    action: t_action_literal | None = None,
) -> None:
    batch: list[StreamedEvent] = []
    reference_time: float = time.monotonic()

    while True:
        await populate_events_batch_from_queue(config, queue, reference_time, batch)
        async with pool.connection() as conn:
            # Perform deduplication
            fresh_event_ids: tuple[int, ...] = await batch_dedup_insert_events(
                conn, (e.event_id for e in batch)
            )
            await trim_duplicate_events(
                redis, batch, fresh_event_ids, stream_name, group_name
            )
            if not batch:
                continue

            inserted_ids: list[int] = []  # Populated in-place by batch_function
            insertion_callable = lambda: batch_function(
                conn, batch, inserted_ids, action
            )

            exception = await retried_event_database_processing(
                conn, config.WORKER.MAX_RETRIES, insertion_callable
            )
            if exception:  # Entire batch failed
                await declare_dead_with_retries(
                    redis,
                    batch,
                    stream_name,
                    group_name,
                    StreamName.DEAD_LETTER_QUEUE,
                    config.WORKER.MAX_RETRIES,
                )
                continue

            successful_events: tuple[StreamedEvent, ...] = tuple(
                event for event in batch if event.event_id in inserted_ids
            )

            # ACK processed events and push failed events to DLQ
            await ack_with_retries(
                redis,
                successful_events,
                stream_name,
                group_name,
                config.WORKER.MAX_RETRIES,
            )
            await declare_dead_with_retries(
                redis,
                tuple(event for event in batch if event not in successful_events),
                stream_name,
                group_name,
                StreamName.DEAD_LETTER_QUEUE,
                config.WORKER.MAX_RETRIES,
            )

            await emit_side_effects_with_retries(
                redis,
                successful_events,
                config.WORKER.MAX_RETRIES,
                stream_name,
                group_name,
            )

            reference_time = time.monotonic()
            batch.clear()


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

                await atomic_emit_side_effects(redis, tuple(batch))

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
