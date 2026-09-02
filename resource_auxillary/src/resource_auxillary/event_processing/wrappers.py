"""Convenience abstractions for common worker operations"""

from typing import Sequence

from resource_auxillary.events import StreamedEvent
from resource_auxillary.strings import StreamName
from resource_auxillary.typing import SupportsExponentialJitteredRetryPolicy
from resource_auxillary.event_processing.qos import (
    dlq_aware_process_events,
    execute_with_redis_retries,
)
from resource_auxillary.event_processing.event_stream_manager import EventStreamManager


async def declare_dead_with_retries(
    event_stream_manager: EventStreamManager,
    retry_policy: SupportsExponentialJitteredRetryPolicy,
    batch: Sequence[StreamedEvent],
    stream_name: StreamName,
    group_name: str,
    dead_letter_stream_name: StreamName,
    *,
    attempts: int | None = None,
) -> None:
    """
    Thin wrapper over sibling utility functions to declare an event batch as dead
    """
    coro = lambda: event_stream_manager.amortize_events(
        batch, stream_name, group_name, dead_letter_stream_name
    )
    await execute_with_redis_retries(retry_policy, coro, attempts)


async def ack_with_retries(
    event_stream_manager: EventStreamManager,
    retry_policy: SupportsExponentialJitteredRetryPolicy,
    batch: Sequence[StreamedEvent],
    stream_name: StreamName,
    group_name: str,
    dead_letter_stream_name: StreamName,
    *,
    attempts: int | None = None,
) -> None:
    """
    Thin wrapper over sibling utility functions to acknowledge an event batch
    """
    coro = lambda: event_stream_manager.acknowledge_events(
        batch, stream_name, group_name
    )
    await dlq_aware_process_events(
        event_stream_manager,
        retry_policy,
        batch,
        coro,
        stream_name,
        group_name,
        dead_letter_stream_name,
        attempts=attempts,
    )
