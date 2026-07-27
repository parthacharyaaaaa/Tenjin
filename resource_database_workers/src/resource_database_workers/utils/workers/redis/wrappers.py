"""Convenience abstractions for common worker operations"""

from typing import Sequence

from redis.asyncio import Redis

from resource_auxillary.events import StreamedEvent
from resource_auxillary.strings import StreamName

from resource_database_workers.config.sub_config import WorkerConfig
from resource_database_workers.utils.workers.redis.post_processing import (
    acknowledge_event,
    amortize_event,
)
from resource_database_workers.utils.workers.redis.qos import (
    dlq_aware_process_events,
    execute_with_redis_retries,
)


async def declare_dead_with_retries(
    redis: Redis,
    worker_config: WorkerConfig,
    batch: Sequence[StreamedEvent],
    stream_name: StreamName,
    group_name: str,
    dead_letter_stream_name: StreamName,
    attempts: int,
) -> None:
    """
    Thin wrapper over sibling utility functions to declare an event as dead
    """
    coro = lambda: amortize_event(
        redis, batch, stream_name, group_name, dead_letter_stream_name
    )
    await execute_with_redis_retries(worker_config, coro, attempts)


async def ack_with_retries(
    redis: Redis,
    worker_config: WorkerConfig,
    batch: Sequence[StreamedEvent],
    stream_name: StreamName,
    group_name: str,
    dead_letter_stream_name: StreamName,
    attempts: int,
) -> None:
    """
    Thin wrapper over sibling utility functions to acknowledge an event
    """
    coro = lambda: acknowledge_event(redis, batch, stream_name, group_name)
    await dlq_aware_process_events(
        redis,
        worker_config,
        batch,
        coro,
        attempts,
        stream_name,
        group_name,
        dead_letter_stream_name,
    )
