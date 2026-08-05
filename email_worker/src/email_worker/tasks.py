"""Emailing tasks"""

import asyncio
import time

from aiosmtplib import SMTP, SMTPException

from psycopg_pool import AsyncConnectionPool

from redis.asyncio import Redis

from resource_auxillary.event_processing.db_qos import batch_dedup_insert_events
from resource_auxillary.event_processing.pre_processing import (
    populate_events_batch_from_queue,
    trim_duplicate_events,
)
from resource_auxillary.event_processing.wrappers import (
    declare_dead_with_retries,
    commit_processed_events,
)
from resource_auxillary.events import StreamedEvent
from resource_auxillary.strings import StreamName

from email_worker.config.email_config import EmailConfig
from email_worker.src.email_worker.utilities.qos import clean_user_email_payloads
from email_worker.dependencies import get_fresh_smtp_client
from email_worker.outgoing import batch_send_emails


async def email_dispatcher(
    email_config: EmailConfig,
    redis: Redis,
    connection_pool: AsyncConnectionPool,
    events_queue: asyncio.Queue[tuple[StreamedEvent]],
    stream_name: StreamName,
    group_name: str,
    dlq_stream_name: StreamName,
) -> None:
    reference_time: float = time.monotonic()
    batch: list[StreamedEvent] = []
    invalid_events_buffer: list[StreamedEvent] = []
    error_data: list[tuple[SMTPException, float]] = []

    while True:
        await populate_events_batch_from_queue(
            email_config.WORKER, events_queue, reference_time, batch
        )

        async with connection_pool.connection() as connection:
            # Event Deduplication
            fresh_event_ids: tuple[int, ...] = await batch_dedup_insert_events(
                connection, (e.event_id for e in batch)
            )
            await trim_duplicate_events(
                redis, batch, fresh_event_ids, stream_name, group_name
            )
            del fresh_event_ids
            if not batch:
                continue

            # Filter out invalid email payloads early
            clean_user_email_payloads(batch, invalid_events_buffer)
            await declare_dead_with_retries(
                redis,
                email_config.WORKER,
                batch,
                stream_name,
                group_name,
                dlq_stream_name,
                email_config.WORKER.MAX_RETRIES,
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

        await commit_processed_events(
            redis, email_config.WORKER, batch, group_name, stream_name, dlq_stream_name
        )

        batch.clear()
        reference_time = time.monotonic()
