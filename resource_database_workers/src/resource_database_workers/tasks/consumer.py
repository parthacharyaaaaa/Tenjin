from resource_database_workers.dependencies.annotations import (
    ACTION_LITERAL,
    ISOLATED_EVENT_QUEUE,
    BATCHED_EVENT_QUEUE,
    STREAM_NAME,
    IDENTIFIER_COLUMN,
    TABLE,
    STATUS_PROXY,
    DEAD_LETTER_STREAM_NAME,
    GROUP_NAME,
    INTERNAL_REDIS,
    CONNECTION_POOL,
    APP_CONFIG,
    EVENT_STREAM_MANAGER,
)
from resource_database_workers.tasks.insertions import batch_insert_with_isolation
from resource_database_workers.tasks.deletions import (
    downstream_soft_delete_strong_entity,
)
from resource_database_workers.tasks.deletions import soft_delete_strong_entity
from datetime import datetime
import time
from typing import Generator

from redis.exceptions import RedisError, ExceptionType

from resource_auxillary.coordination import exponential_jittered_backoff
from resource_auxillary.datastructures.database import StrongEntity
from resource_auxillary.events import StreamedEvent
from resource_auxillary.event_processing.pre_processing import (
    populate_events_batch_from_queue,
)
from resource_auxillary.event_processing.qos import execute_with_redis_retries
from resource_auxillary.event_processing.wrappers import (
    ack_with_retries,
    declare_dead_with_retries,
)
from resource_auxillary.constants import POTENTIAL_TRANSIENT_ERRORS

from resource_auxillary.event_processing.db_qos import (
    batch_dedup_insert_events,
    dedup_insert_event,
    db_execute_with_retries,
)
from resource_database_workers.workers.redis.downstream_post_processing import (
    dispatch_downstream_counter_decrements,
    dispatch_downstream_events,
    emit_downstream_counter_decrement_updates,
)
from resource_database_workers.tasks.selections import select_decrement_deltas
from resource_database_workers.datastructures.downstream import (
    DownstreamCounterDecrementData,
    DownstreamDeletionData,
    reconstruct_downstream_counter_data_from_stream,
    reconstruct_downstream_data_from_stream,
)


async def user_orphan_consumer(
    config: APP_CONFIG,
    pool: CONNECTION_POOL,
    event_stream_manager: EVENT_STREAM_MANAGER,
    queue: BATCHED_EVENT_QUEUE,
    stream_name: STREAM_NAME,
    group_name: GROUP_NAME,
    dead_letter_stream_name: DEAD_LETTER_STREAM_NAME,
    status_proxy: STATUS_PROXY,
) -> None:
    batch: list[StreamedEvent] = []
    reference_time: float = time.monotonic()

    while status_proxy.status_ok:
        await populate_events_batch_from_queue(
            config.WORKER, queue, reference_time, batch
        )

        # Database connection only needed for deduplication
        async with pool.connection() as conn:
            fresh_event_ids: tuple[int, ...] = await batch_dedup_insert_events(
                conn, (e.event_id for e in batch), batch[0].name
            )

        await event_stream_manager.trim_duplicate_events(
            batch, fresh_event_ids, stream_name, group_name
        )

        exception: Exception | None = None
        for _attempt in range(1, config.WORKER.MAX_RETRIES + 1):
            try:
                await dispatch_downstream_events(
                    event_stream_manager,
                    config.WORKER,
                    StrongEntity.USER,
                    (
                        (event.payload["user_id"], event.payload["time_deleted"])
                        for event in batch
                    ),
                    dead_letter_stream_name,
                )
                exception = None
                break
            except RedisError as redis_error:
                exception = redis_error
                if redis_error.error_type == ExceptionType.NETWORK:
                    await exponential_jittered_backoff(
                        config.WORKER.MAXIMUM_BACKOFF_INTERVAL,
                        config.WORKER.BASE_BACKOFF_INTERVAL,
                        _attempt,
                        exponential=config.WORKER.BACKOFF_EXPONENTIAL,
                    )
                    continue
                break
            except Exception as e:
                exception = e
                break

        if exception:  # Entire batch failed
            await declare_dead_with_retries(
                event_stream_manager,
                config.WORKER,
                batch,
                stream_name,
                group_name,
                dead_letter_stream_name,
            )
        else:
            # ACK entire batch
            await ack_with_retries(
                event_stream_manager,
                config.WORKER,
                batch,
                stream_name,
                group_name,
                dead_letter_stream_name,
            )

        batch.clear()
        reference_time = time.monotonic()


async def queue_insertion_consumer(
    config: APP_CONFIG,
    pool: CONNECTION_POOL,
    event_stream_manager: EVENT_STREAM_MANAGER,
    queue: BATCHED_EVENT_QUEUE,
    stream_name: STREAM_NAME,
    group_name: GROUP_NAME,
    dead_letter_stream_name: DEAD_LETTER_STREAM_NAME,
    status_proxy: STATUS_PROXY,
    action: ACTION_LITERAL = None,
) -> None:
    batch: list[StreamedEvent] = []
    reference_time: float = time.monotonic()

    while status_proxy.status_ok:
        await populate_events_batch_from_queue(
            config.WORKER, queue, reference_time, batch
        )
        async with pool.connection() as conn:
            # Perform deduplication
            fresh_event_ids: tuple[int, ...] = await batch_dedup_insert_events(
                conn, (e.event_id for e in batch), batch[0].name
            )
            await event_stream_manager.trim_duplicate_events(
                batch, fresh_event_ids, stream_name, group_name
            )
            if not batch:
                continue

            inserted_ids: list[int] = []  # Populated in-place by batch_function
            insertion_callable = lambda: batch_insert_with_isolation(
                conn, batch, inserted_ids, action
            )
            try:
                await db_execute_with_retries(config.WORKER, conn, insertion_callable)
            except Exception:  # Entire batch failed
                await declare_dead_with_retries(
                    event_stream_manager,
                    config.WORKER,
                    batch,
                    stream_name,
                    group_name,
                    dead_letter_stream_name,
                )
                continue
            successful_events: tuple[StreamedEvent, ...] = tuple(
                event for event in batch if event.event_id in inserted_ids
            )

            # post-process successful events and push failed events to DLQ
            await ack_with_retries(
                event_stream_manager,
                config.WORKER,
                batch,
                stream_name,
                group_name,
                dead_letter_stream_name,
            )
            await declare_dead_with_retries(
                event_stream_manager,
                config.WORKER,
                tuple(event for event in batch if event not in successful_events),
                stream_name,
                group_name,
                dead_letter_stream_name,
            )

            reference_time = time.monotonic()
            batch.clear()


async def queue_deletion_consumer(
    config: APP_CONFIG,
    pool: CONNECTION_POOL,
    event_stream_manager: EVENT_STREAM_MANAGER,
    table: TABLE,
    identifier_column: IDENTIFIER_COLUMN,
    queue: BATCHED_EVENT_QUEUE,
    stream_name: STREAM_NAME,
    group_name: GROUP_NAME,
    dead_letter_stream_name: DEAD_LETTER_STREAM_NAME,
    status_proxy: STATUS_PROXY,
) -> None:
    batch: list[StreamedEvent] = []
    reference_time: float = time.monotonic()

    while status_proxy.status_ok:
        await populate_events_batch_from_queue(
            config.WORKER, queue, reference_time, batch
        )
        async with pool.connection() as conn:
            fresh_event_ids: tuple[int, ...] = await batch_dedup_insert_events(
                conn, (e.event_id for e in batch), batch[0].name
            )
            await event_stream_manager.trim_duplicate_events(
                batch, fresh_event_ids, stream_name, group_name
            )

            deletion_data: Generator[tuple[int, datetime, int]] = (
                (
                    event.payload[identifier_column],
                    event.payload["deleted_at"],
                    event.event_id,
                )
                for event in batch
            )

            deletion_callable = lambda: soft_delete_strong_entity(
                conn, table.value, identifier_column, deletion_data
            )
            try:
                await db_execute_with_retries(config.WORKER, conn, deletion_callable)
            except Exception:
                await declare_dead_with_retries(
                    event_stream_manager,
                    config.WORKER,
                    batch,
                    stream_name,
                    group_name,
                    dead_letter_stream_name,
                )
                continue
            # ACK entire batch and emit side-effects
            await ack_with_retries(
                event_stream_manager,
                config.WORKER,
                batch,
                stream_name,
                group_name,
                dead_letter_stream_name,
            )
            await dispatch_downstream_events(
                event_stream_manager,
                config.WORKER,
                table,
                (
                    (event.payload[identifier_column], event.payload["deleted_at"])
                    for event in batch
                ),
                dead_letter_stream_name,
            )

            batch.clear()
            reference_time = time.monotonic()


async def queue_downstream_deletion_consumer(
    config: APP_CONFIG,
    pool: CONNECTION_POOL,
    event_stream_manager: EVENT_STREAM_MANAGER,
    queue: ISOLATED_EVENT_QUEUE,
    stream_name: STREAM_NAME,
    group_name: GROUP_NAME,
    dead_letter_stream_name: DEAD_LETTER_STREAM_NAME,
    status_proxy: STATUS_PROXY,
) -> None:
    while status_proxy.status_ok:
        event: StreamedEvent = await queue.get()
        try:
            event_payload: DownstreamDeletionData = (
                reconstruct_downstream_data_from_stream(event.payload)
            )
        except (KeyError, ValueError):
            await declare_dead_with_retries(
                event_stream_manager,
                config.WORKER,
                (event,),
                stream_name,
                group_name,
                dead_letter_stream_name,
            )
            continue

        async with pool.connection() as conn:
            # Deduplication
            if not await dedup_insert_event(conn, event.event_id, event.name):
                await event_stream_manager.acknowledge_events(
                    (event,), stream_name, group_name
                )
                continue

            downstream_deletion_callable = lambda: downstream_soft_delete_strong_entity(
                conn,
                event_payload["foreign_key"],
                event_payload["orphan_table"],
                event_payload["foreign_key_column"],
                event_payload["deleted_at"],
            )

            try:
                await db_execute_with_retries(
                    config.WORKER, conn, downstream_deletion_callable
                )
            except Exception:
                # Single event tuple used in place of event
                # for methods that process batches of events
                await declare_dead_with_retries(
                    event_stream_manager,
                    config.WORKER,
                    (event,),
                    stream_name,
                    group_name,
                    dead_letter_stream_name,
                )
                continue

            await ack_with_retries(
                event_stream_manager,
                config.WORKER,
                (event,),
                stream_name,
                group_name,
                dead_letter_stream_name,
            )

            await dispatch_downstream_counter_decrements(
                event_stream_manager,
                config.WORKER,
                event_payload["orphan_table"],
                event.event_id,
                dead_letter_stream_name,
            )


async def queue_downstream_decrement_consumer(
    config: APP_CONFIG,
    pool: CONNECTION_POOL,
    redis: INTERNAL_REDIS,
    event_stream_manager: EVENT_STREAM_MANAGER,
    queue: ISOLATED_EVENT_QUEUE,
    stream_name: STREAM_NAME,
    group_name: GROUP_NAME,
    dead_letter_stream_name: DEAD_LETTER_STREAM_NAME,
    status_proxy: STATUS_PROXY,
) -> None:
    while status_proxy.status_ok:
        event: StreamedEvent = await queue.get()
        try:
            event_payload: DownstreamCounterDecrementData = (
                reconstruct_downstream_counter_data_from_stream(event.payload)
            )
        except (KeyError, ValueError):
            await declare_dead_with_retries(
                event_stream_manager,
                config.WORKER,
                (event,),
                stream_name,
                group_name,
                dead_letter_stream_name,
            )
            continue

        # Downstream counters may be too big to materialize all at once
        limit, offset = config.WORKER.DOWNSTREAM_COUNTER_BATCH_SIZE, 0
        exception: Exception | None = None
        async with pool.connection() as conn:
            if not await dedup_insert_event(conn, event.event_id, event.name):
                await event_stream_manager.acknowledge_events(
                    (event,), stream_name, group_name
                )
                continue

            for _attempt in range(1, config.WORKER.MAX_RETRIES + 1):
                try:
                    # temp truthy tuple to enter loop
                    # (hehe the initial list kinda looks like a wink)
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

                        emission_coroutine = (
                            lambda: emit_downstream_counter_decrement_updates(
                                redis,
                                results,
                                event_payload["hashmap_name"],
                                event_payload["affected_table_name"],
                            )
                        )
                        await execute_with_redis_retries(
                            config.WORKER, emission_coroutine
                        )
                except POTENTIAL_TRANSIENT_ERRORS as e:
                    exception = e
                    await conn.rollback()
                    await exponential_jittered_backoff(
                        config.WORKER.MAXIMUM_BACKOFF_INTERVAL,
                        config.WORKER.BASE_BACKOFF_INTERVAL,
                        _attempt,
                        exponential=config.WORKER.BACKOFF_EXPONENTIAL,
                    )
                    continue
                except Exception as e:
                    exception = e
                    await conn.rollback()
                    break

            if exception:
                await declare_dead_with_retries(
                    event_stream_manager,
                    config.WORKER,
                    (event,),
                    stream_name,
                    group_name,
                    dead_letter_stream_name,
                )
            else:
                await ack_with_retries(
                    event_stream_manager,
                    config.WORKER,
                    (event,),
                    stream_name,
                    group_name,
                    dead_letter_stream_name,
                )
