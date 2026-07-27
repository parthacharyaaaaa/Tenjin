"""Pre-processing functions for streamed events"""

import asyncio
import time
from typing import Sequence

from redis.asyncio import Redis

from resource_auxillary.strings import StreamName
from resource_auxillary.events import StreamedEvent

from resource_database_workers.config.config import AppConfig


async def trim_duplicate_events(
    redis: Redis,
    batch: list[StreamedEvent],
    fresh_event_ids: Sequence[int],
    stream_name: StreamName,
    group_name: str,
) -> None:
    async with redis.pipeline() as pipeline:
        for event in batch.copy():
            if event.event_id not in fresh_event_ids:
                batch.remove(event)
                pipeline.xack(stream_name, group_name, event.event_id)
        await pipeline.execute()


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
