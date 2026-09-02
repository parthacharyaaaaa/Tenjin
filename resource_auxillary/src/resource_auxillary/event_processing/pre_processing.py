"""Pre-processing functions for streamed events"""

import asyncio
import time

from resource_auxillary.events import StreamedEvent
from resource_auxillary.typing import SupportsInternalQueueConsumerPolicy


async def populate_events_batch_from_queue(
    batching_policy: SupportsInternalQueueConsumerPolicy,
    queue: asyncio.Queue[tuple[StreamedEvent, ...]],
    reference_time: float,
    batch: list[StreamedEvent],
) -> None:
    while True:
        if not (
            (len(batch) >= batching_policy.IQ_CONSUMER_BATCH_SIZE_QUOTA)
            or time.monotonic() - reference_time
            > batching_policy.IQ_CONSUMER_BASE_WAITING_TIME
        ):
            try:
                new_entries: tuple[StreamedEvent, ...] = await asyncio.wait_for(
                    queue.get(), batching_policy.IQ_CONSUMER_GET_TIMEOUT
                )
                if not batch:
                    reference_time = time.monotonic()
                batch.extend(new_entries)
            except asyncio.TimeoutError:
                await asyncio.sleep(batching_policy.IQ_CONSUMER_SLEEP_INTERVAL)
            continue

        if not batch:
            await asyncio.sleep(batching_policy.IQ_CONSUMER_SLEEP_INTERVAL)
            reference_time = time.monotonic()
            continue
