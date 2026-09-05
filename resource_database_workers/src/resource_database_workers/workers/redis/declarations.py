"""Utility functions for declaring new events"""

from resource_auxillary.typing import SupportsExponentialJitteredRetryPolicy
from typing import Sequence

from auxillary.utils import json_repr

from resource_auxillary.events import Event, StreamedEvent
from resource_auxillary.event_processing.event_stream_manager import EventStreamManager
from resource_auxillary.event_processing.qos import execute_with_redis_retries
from resource_auxillary.strings import NAME_SEPERATOR, EventName, StreamName

from resource_database_workers.datastructures.dead_counter_batch import DeadCounterBatch


async def declare_counters_event_dead(
    stream_manager: EventStreamManager,
    retry_policy: SupportsExponentialJitteredRetryPolicy,
    dlq_stream_name: StreamName,
    counter_group: str,
    batch: dict[int, int],
) -> None:
    table, column = counter_group.split(NAME_SEPERATOR)
    dlq_counters_batch: DeadCounterBatch = DeadCounterBatch.construct_from_failed_batch(
        table, column, batch
    )
    failure_event: Event = Event(
        name=EventName.DLQ_COUNTER,
        payload=json_repr(dlq_counters_batch),
        side_effects=EventSideEffects(),  # type: ignore
    )

    dlq_coroutine = lambda: stream_manager.stream_events(
        (failure_event,), dlq_stream_name
    )
    await execute_with_redis_retries(retry_policy, dlq_coroutine)


async def declare_side_effects_event_dead(
    stream_manager: EventStreamManager,
    retry_policy: SupportsExponentialJitteredRetryPolicy,
    batch: Sequence[StreamedEvent],
    dlq_stream_name: StreamName,
    attempts: int,
) -> None:
    failure_events: tuple[Event, ...] = tuple(
        Event(
            name=EventName.DLQ_SIDE_EFFECTS,
            payload=json_repr(event),
            side_effects=EventSideEffects(),  # type: ignore
        )
        for event in batch
    )

    dlq_coroutine = lambda: stream_manager.stream_events(
        failure_events, dlq_stream_name
    )
    await execute_with_redis_retries(retry_policy, dlq_coroutine, attempts)
