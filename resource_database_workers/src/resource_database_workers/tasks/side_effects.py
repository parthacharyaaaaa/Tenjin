from resource_database_workers.utils.db import mark_side_effect_row_processed
from resource_database_workers.workers.redis.downstream_post_processing import (
    register_counter_decrement_updates,
)
from resource_auxillary.events import EventSideEffects
from resource_auxillary.strings import EventName
from resource_auxillary.events import Event
from pydantic import ValidationError
from resource_auxillary.datastructures.database import SideEffectsTables
from resource_database_workers.dependencies.annotations import (
    STATUS_PROXY,
    DEAD_LETTER_STREAM_NAME,
    INTERNAL_REDIS,
    CONNECTION_POOL,
    APP_CONFIG,
    EVENT_STREAM_MANAGER,
)
from resource_database_workers.tasks.deletions import (
    downstream_soft_delete_strong_entity,
)


from resource_auxillary.coordination import exponential_jittered_backoff
from resource_auxillary.event_processing.qos import execute_with_redis_retries
from resource_auxillary.constants import POTENTIAL_TRANSIENT_ERRORS

from resource_auxillary.event_processing.db_qos import (
    db_execute_with_retries,
)
from resource_database_workers.tasks.selections import select_decrement_deltas
from resource_database_workers.datastructures.side_effects import (
    DownstreamDeletionPayload,
    DownstreamDecrementPayload,
)
from resource_database_workers.utils.db import get_side_effect_row


async def downstream_deletion_worker(
    config: APP_CONFIG,
    pool: CONNECTION_POOL,
    event_stream_manager: EVENT_STREAM_MANAGER,
    dead_letter_stream_name: DEAD_LETTER_STREAM_NAME,
    status_proxy: STATUS_PROXY,
) -> None:
    side_effects_retrieval_coroutine = lambda: get_side_effect_row(
        conn, SideEffectsTables.DOWNSTREAM_DELETION
    )
    while status_proxy.status_ok:
        async with pool.connection() as conn:
            event_id, raw_payload = await db_execute_with_retries(
                config.WORKER, conn, side_effects_retrieval_coroutine
            )
            try:
                payload: DownstreamDeletionPayload = (
                    DownstreamDeletionPayload.model_construct(**raw_payload)
                )
            except (ValueError, ValidationError, KeyError):
                dead_event: Event = Event(
                    name=EventName.DLQ_SIDE_EFFECTS,
                    payload={"event_id": event_id},
                    side_effects=EventSideEffects(),
                )
                emission_coroutine = lambda: event_stream_manager.stream_events(
                    (dead_event,), dead_letter_stream_name
                )
                await execute_with_redis_retries(config.WORKER, emission_coroutine)
                continue
            del raw_payload

            downstream_deletion_callable = lambda: downstream_soft_delete_strong_entity(
                conn,
                event_id,
                payload.foreign_key,
                payload.orphan_table,
                payload.foreign_key_column,
                payload.deleted_at,
            )
            outbox_processing_coroutine = lambda: mark_side_effect_row_processed(
                conn, SideEffectsTables.DOWNSTREAM_DELETION, event_id
            )
            try:
                await db_execute_with_retries(
                    config.WORKER, conn, downstream_deletion_callable
                )
                await db_execute_with_retries(
                    config.WORKER, conn, outbox_processing_coroutine
                )
            except Exception:
                dead_event: Event = Event(
                    name=EventName.DLQ_SIDE_EFFECTS,
                    payload={"event_id": event_id},
                    side_effects=EventSideEffects(),
                )
                emission_coroutine = lambda: event_stream_manager.stream_events(
                    (dead_event,), dead_letter_stream_name
                )
                await execute_with_redis_retries(config.WORKER, emission_coroutine)


async def downstream_decrement_worker(
    config: APP_CONFIG,
    pool: CONNECTION_POOL,
    redis: INTERNAL_REDIS,
    event_stream_manager: EVENT_STREAM_MANAGER,
    dead_letter_stream_name: DEAD_LETTER_STREAM_NAME,
    status_proxy: STATUS_PROXY,
) -> None:
    while status_proxy.status_ok:
        async with pool.connection() as conn:
            side_effects_retrieval_coroutine = lambda: get_side_effect_row(
                conn, SideEffectsTables.DOWNSTREAM_DECREMENT
            )
            event_id, raw_payload = await db_execute_with_retries(
                config.WORKER, conn, side_effects_retrieval_coroutine
            )
            try:
                payload: DownstreamDecrementPayload = (
                    DownstreamDecrementPayload.model_construct(**raw_payload)
                )
            except (ValueError, ValidationError, KeyError):
                dead_event: Event = Event(
                    name=EventName.DLQ_SIDE_EFFECTS,
                    payload={"event_id": event_id},
                    side_effects=EventSideEffects(),
                )
                emission_coroutine = lambda: event_stream_manager.stream_events(
                    (dead_event,), dead_letter_stream_name
                )
                await execute_with_redis_retries(config.WORKER, emission_coroutine)
                continue
            del raw_payload

        # Downstream counters may be too big to materialize all at once
        limit, offset = config.WORKER.DOWNSTREAM_COUNTER_BATCH_SIZE, 0
        for _attempt in range(1, config.WORKER.MAX_RETRIES + 1):
            async with redis.pipeline(transaction=True) as pipeline:
                try:
                    # temp truthy tuple to enter loop
                    # (hehe the initial list kinda looks like a wink)
                    results: list[tuple[str, int]] = [("", 0)]
                    while results:
                        # Fetch subset of counter deltas
                        results: list[tuple[str, int]] = await select_decrement_deltas(
                            conn,
                            payload.foreign_key_column,
                            limit,
                            offset,
                            payload.orphaned_table,
                            event_id,
                        )
                        offset += limit
                        # Buffer HINCRBY commands to pipeline
                        register_counter_decrement_updates(
                            pipeline,
                            results,
                            payload.hashmap_name,
                            payload.orphaned_table,
                        )
                    await pipeline.execute()
                    outbox_processing_coroutine = (
                        lambda: mark_side_effect_row_processed(
                            conn, SideEffectsTables.DOWNSTREAM_DECREMENT, event_id
                        )
                    )
                    await db_execute_with_retries(
                        config.WORKER, conn, outbox_processing_coroutine
                    )
                except POTENTIAL_TRANSIENT_ERRORS as e:
                    if _attempt == config.WORKER.MAX_RETRIES:
                        raise e
                    await conn.rollback()
                    await exponential_jittered_backoff(
                        config.WORKER.MAXIMUM_BACKOFF_INTERVAL,
                        config.WORKER.BASE_BACKOFF_INTERVAL,
                        _attempt,
                        exponential=config.WORKER.BACKOFF_EXPONENTIAL,
                    )
                    # Reset selection params
                    limit, offset = config.WORKER.DOWNSTREAM_COUNTER_BATCH_SIZE, 0
                    continue
                except Exception:
                    dead_event: Event = Event(
                        name=EventName.DLQ_SIDE_EFFECTS,
                        payload={"event_id": event_id},
                        side_effects=EventSideEffects(),
                    )
                    emission_coroutine = lambda: event_stream_manager.stream_events(
                        (dead_event,), dead_letter_stream_name
                    )
                    await execute_with_redis_retries(config.WORKER, emission_coroutine)
