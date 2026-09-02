from resource_database_workers.dependencies.annotations import ISOLATED_EVENT_QUEUE
from typing import Any, Sequence

from psycopg import AsyncConnection
from psycopg.sql import Composed

from auxillary.utils import json_repr

from resource_auxillary.events import (
    CacheUpdate,
    CounterUpdate,
    IntentUpdate,
    StreamedEvent,
)
from resource_auxillary.event_processing.db_qos import (
    db_execute_with_retries,
    dedup_insert_event,
)
from resource_auxillary.event_processing.qos import execute_with_redis_retries
from resource_auxillary.datastructures.database import SideEffectType
from resource_auxillary.strings import EventName

from resource_database_workers.datastructures.dead_counter_batch import DeadCounterBatch
from resource_database_workers.dependencies.annotations import (
    APP_CONFIG,
    DEAD_LETTER_STREAM_NAME,
    CONNECTION_POOL,
    GROUP_NAME,
    STATUS_PROXY,
    EVENT_STREAM_MANAGER,
)


def get_dlq_insertion_parameters(event: StreamedEvent) -> tuple[Any, ...]:
    if event.name == EventName.DLQ_COUNTER:
        dead_counter_batch: DeadCounterBatch = (
            DeadCounterBatch.construct_from_event_payload(event.payload)
        )
        return (
            dead_counter_batch.table,
            dead_counter_batch.column,
            dead_counter_batch.failure_time,
            dead_counter_batch.counters,
        )
    elif event.name == EventName.DLQ_SIDE_EFFECTS:
        side_effect_groups: tuple[
            tuple[
                SideEffectType, tuple[CounterUpdate | IntentUpdate | CacheUpdate, ...]
            ],
            ...,
        ] = (
            (
                SideEffectType.CACHE_INVALIDATION,
                event.side_effects.cache_invalidations,
            ),
            (
                SideEffectType.COUNTER_UPDATE,
                event.side_effects.counter_updates,
            ),
            (
                SideEffectType.INTENT_INVALIDATION,
                event.side_effects.intent_updates,
            ),
        )
        return tuple(
            (event.event_id, side_effect_type.value, json_repr(side_effect))
            for (side_effect_type, side_effects) in side_effect_groups
            for side_effect in side_effects
        )
    else:  # Standard failed StreamedEvent
        return (event.event_id, json_repr(event))


async def _insert_dlq_record(
    connection: AsyncConnection,
    composed_statement: Composed,
    insertion_parameters: Sequence[Any],
) -> None:
    await connection.execute(composed_statement, insertion_parameters)
    await connection.commit()


async def dlq_consumer(
    config: APP_CONFIG,
    stream_name: DEAD_LETTER_STREAM_NAME,
    pool: CONNECTION_POOL,
    event_stream_manager: EVENT_STREAM_MANAGER,
    group_name: GROUP_NAME,
    queue: ISOLATED_EVENT_QUEUE,
    composed_statement: Composed,
    status_proxy: STATUS_PROXY,
) -> None:
    while status_proxy.status_ok:
        dlq_event: StreamedEvent = await queue.get()
        async with pool.connection() as conn:
            # Apply deduplication

            if not await dedup_insert_event(conn, dlq_event.event_id):
                # Retry, but appending back to DLQ is pointless in a DLQ worker
                await execute_with_redis_retries(
                    config.WORKER,
                    lambda: event_stream_manager.acknowledge_events(
                        (dlq_event,), stream_name, group_name
                    ),
                )

            # !duplicate event
            insertion_params: tuple[Any, ...] = get_dlq_insertion_parameters(dlq_event)
            db_coroutine = lambda: _insert_dlq_record(
                conn, composed_statement, insertion_params
            )
            await db_execute_with_retries(config.WORKER, conn, db_coroutine)
            await execute_with_redis_retries(
                config.WORKER,
                lambda: event_stream_manager.acknowledge_events(
                    (dlq_event,), stream_name, group_name
                ),
            )
