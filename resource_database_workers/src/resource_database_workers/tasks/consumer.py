from resource_auxillary.datastructures.database import GenericLiterals
from resource_database_workers.tasks.insertions import (
    downstream_deletion_outbox_insertion,
)
from resource_database_workers.tasks.insertions import outbox_insertion
from resource_database_workers.dependencies.annotations import (
    ACTION_LITERAL,
    BATCHED_EVENT_QUEUE,
    STREAM_NAME,
    IDENTIFIER_COLUMN,
    TABLE,
    STATUS_PROXY,
    DEAD_LETTER_STREAM_NAME,
    GROUP_NAME,
    CONNECTION_POOL,
    APP_CONFIG,
    EVENT_STREAM_MANAGER,
)
from resource_database_workers.tasks.insertions import batch_insert_with_isolation
from resource_database_workers.tasks.deletions import soft_delete_strong_entity
from datetime import datetime
import time
from typing import Generator


from resource_auxillary.datastructures.database import StrongEntity
from resource_auxillary.events import StreamedEvent
from resource_auxillary.event_processing.pre_processing import (
    populate_events_batch_from_queue,
)
from resource_auxillary.event_processing.wrappers import (
    ack_with_retries,
    declare_dead_with_retries,
)

from resource_auxillary.event_processing.db_qos import (
    batch_dedup_insert_events,
    db_execute_with_retries,
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

            # Implicit events not in the network payload (downstream deletion only in this case)
            downstream_deletion_outbox_callable = (
                lambda: downstream_deletion_outbox_insertion(
                    conn, batch, StrongEntity.USER, GenericLiterals.ID
                )
            )
            try:
                await db_execute_with_retries(
                    config.WORKER, conn, downstream_deletion_outbox_callable
                )
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
                successful_events: tuple[StreamedEvent, ...] = tuple(
                    event for event in batch if event.event_id in inserted_ids
                )
                outbox_insertion_callable = lambda: outbox_insertion(
                    conn, successful_events
                )
                await db_execute_with_retries(
                    config.WORKER, conn, outbox_insertion_callable
                )
                await conn.commit()
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

        # post-process successful events and push failed events to DLQ
        await ack_with_retries(
            event_stream_manager,
            config.WORKER,
            successful_events,
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
                outbox_insertion_callable = lambda: outbox_insertion(conn, batch)
                await db_execute_with_retries(
                    config.WORKER, conn, outbox_insertion_callable
                )

                # Implicit events not in the network payload (downstream deletion only in this case)
                downstream_deletion_outbox_callable = (
                    lambda: downstream_deletion_outbox_insertion(
                        conn, batch, table, identifier_column
                    )
                )
                await db_execute_with_retries(
                    config.WORKER, conn, downstream_deletion_outbox_callable
                )
                await conn.commit()
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

        batch.clear()
        reference_time = time.monotonic()
