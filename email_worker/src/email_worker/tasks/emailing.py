"""Emailing tasks"""

import time

from aiosmtplib import SMTP, SMTPException
from resource_auxillary.event_processing.db_qos import batch_dedup_insert_events
from resource_auxillary.event_processing.pre_processing import (
    populate_events_batch_from_queue,
)
from resource_auxillary.event_processing.wrappers import (
    ack_with_retries,
    declare_dead_with_retries,
)
from resource_auxillary.events import StreamedEvent

from email_worker.dependencies.annotations import (
    BATCHED_EVENT_QUEUE,
    CONNECTION_POOL,
    DEAD_LETTER_STREAM_NAME,
    EMAIL_CONFIG,
    EVENT_STREAM_MANAGER,
    GROUP_NAME,
    STATUS_PROXY,
    STREAM_NAME,
)
from email_worker.dependencies.injections import get_fresh_smtp_client
from email_worker.outgoing import batch_send_emails
from email_worker.utilities.qos import clean_user_email_payloads


async def email_dispatcher(
    email_config: EMAIL_CONFIG,
    event_stream_manager: EVENT_STREAM_MANAGER,
    connection_pool: CONNECTION_POOL,
    events_queue: BATCHED_EVENT_QUEUE,
    stream_name: STREAM_NAME,
    group_name: GROUP_NAME,
    dlq_stream_name: DEAD_LETTER_STREAM_NAME,
    status_proxy: STATUS_PROXY,
) -> None:
    reference_time: float = time.monotonic()
    batch: list[StreamedEvent] = []
    invalid_events_buffer: list[StreamedEvent] = []
    error_data: list[tuple[SMTPException, float]] = []

    while status_proxy.status_ok:
        await populate_events_batch_from_queue(
            email_config.WORKER, events_queue, reference_time, batch
        )

        async with connection_pool.connection() as connection:
            # Event Deduplication
            fresh_event_ids: tuple[int, ...] = await batch_dedup_insert_events(
                connection, (e.event_id for e in batch), batch[0].name
            )
            await event_stream_manager.trim_duplicate_events(
                batch, fresh_event_ids, stream_name, group_name
            )
            del fresh_event_ids
            if not batch:
                continue

            # Filter out invalid email payloads early
            clean_user_email_payloads(batch, invalid_events_buffer)
            await declare_dead_with_retries(
                event_stream_manager,
                email_config.WORKER,
                batch,
                stream_name,
                group_name,
                dlq_stream_name,
            )
            invalid_events_buffer.clear()

            smtp_client: SMTP = await get_fresh_smtp_client()
            await batch_send_emails(
                email_config,
                smtp_client,
                batch,
                email_config.WORKER.MAX_RETRIES,
                error_data,
            )
            await connection.commit()

        await ack_with_retries(
            event_stream_manager,
            email_config.WORKER,
            batch,
            stream_name,
            group_name,
            dlq_stream_name,
        )

        batch.clear()
        reference_time = time.monotonic()
