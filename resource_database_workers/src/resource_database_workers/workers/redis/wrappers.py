"""Convenience abstractions for common worker operations"""

from typing import Sequence

from redis.asyncio import Redis

from resource_auxillary.events import StreamedEvent
from resource_auxillary.strings import StreamName
from resource_auxillary.typing import SupportsExponentialJitteredRetryPolicy

from resource_database_workers.workers.redis.post_processing import (
    acknowledge_event,
    amortize_event,
    atomic_ack_and_emit_side_effects,
)
from resource_database_workers.workers.redis.qos import (
    dlq_aware_process_events,
    execute_with_redis_retries,
)


async def declare_dead_with_retries(
    redis: Redis,
    retry_policy: SupportsExponentialJitteredRetryPolicy,
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
    await execute_with_redis_retries(retry_policy, coro, attempts)


async def ack_with_retries(
    redis: Redis,
    retry_policy: SupportsExponentialJitteredRetryPolicy,
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
        retry_policy,
        batch,
        coro,
        attempts,
        stream_name,
        group_name,
        dead_letter_stream_name,
    )


async def commit_processed_events(
    redis: Redis,
    retry_policy: SupportsExponentialJitteredRetryPolicy,
    events: Sequence[StreamedEvent],
    group_name: str,
    stream_name: StreamName,
    dlq_stream_name: StreamName,
) -> None:
    """
    Thin wrapper to atomically acknowledge a collection of events and
    emit their side-effects
    """
    coro = lambda: atomic_ack_and_emit_side_effects(
        redis, events, stream_name, group_name
    )
    await dlq_aware_process_events(
        redis,
        retry_policy,
        events,
        coro,
        retry_policy.MAX_RETRIES,
        stream_name,
        group_name,
        dlq_stream_name,
    )
