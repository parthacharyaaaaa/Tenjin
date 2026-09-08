from resource_database_workers.dependencies.annotations import (
    ISOLATED_EVENT_QUEUE,
    STREAM_NAME,
    STATUS_PROXY,
    DEAD_LETTER_STREAM_NAME,
    GROUP_NAME,
    INTERNAL_REDIS,
    CONNECTION_POOL,
    APP_CONFIG,
    EVENT_STREAM_MANAGER,
)
from resource_database_workers.tasks.deletions import (
    downstream_soft_delete_strong_entity,
)


from resource_auxillary.coordination import exponential_jittered_backoff
from resource_auxillary.events import StreamedEvent
from resource_auxillary.event_processing.qos import execute_with_redis_retries
from resource_auxillary.event_processing.wrappers import (
    ack_with_retries,
    declare_dead_with_retries,
)
from resource_auxillary.constants import POTENTIAL_TRANSIENT_ERRORS

from resource_auxillary.event_processing.db_qos import (
    dedup_insert_event,
    db_execute_with_retries,
)
from resource_database_workers.workers.redis.downstream_post_processing import (
    emit_downstream_counter_decrement_updates,
)
from resource_database_workers.tasks.selections import select_decrement_deltas
from resource_database_workers.datastructures.downstream import (
    DownstreamCounterDecrementData,
    DownstreamDeletionData,
    reconstruct_downstream_counter_data_from_stream,
    reconstruct_downstream_data_from_stream,
)


async def downstream_deletion_worker(
    config: APP_CONFIG,
    pool: CONNECTION_POOL,
    event_stream_manager: EVENT_STREAM_MANAGER,
    queue: ISOLATED_EVENT_QUEUE,
    stream_name: STREAM_NAME,
    group_name: GROUP_NAME,
    dead_letter_stream_name: DEAD_LETTER_STREAM_NAME,
    status_proxy: STATUS_PROXY,
) -> None:
    while status_proxy.status_ok:
        event: StreamedEvent = await queue.get()
        try:
            event_payload: DownstreamDeletionData = (
                reconstruct_downstream_data_from_stream(event.payload)
            )
        except (KeyError, ValueError):
            await declare_dead_with_retries(
                event_stream_manager,
                config.WORKER,
                (event,),
                stream_name,
                group_name,
                dead_letter_stream_name,
            )
            continue

        async with pool.connection() as conn:
            # Deduplication
            if not await dedup_insert_event(conn, event.event_id, event.name):
                await event_stream_manager.acknowledge_events(
                    (event,), stream_name, group_name
                )
                continue

            downstream_deletion_callable = lambda: downstream_soft_delete_strong_entity(
                conn,
                event.event_id,
                event_payload["foreign_key"],
                event_payload["orphan_table"],
                event_payload["foreign_key_column"],
                event_payload["deleted_at"],
            )

            try:
                await db_execute_with_retries(
                    config.WORKER, conn, downstream_deletion_callable
                )
            except Exception:
                await conn.rollback()


# TODO: This function will be an outbox consumer instead of a Redis stream consumer,
# And we'll need to manage idempotency/atomicity for insanely large downstream workloads
# (e.g. Forum deletion -> decrement post count per user for every post EVER!!!).
# God, this is gonna be a pain to revamp >:((((
async def downstream_decrement_worker(
    config: APP_CONFIG,
    pool: CONNECTION_POOL,
    redis: INTERNAL_REDIS,
    event_stream_manager: EVENT_STREAM_MANAGER,
    queue: ISOLATED_EVENT_QUEUE,
    stream_name: STREAM_NAME,
    group_name: GROUP_NAME,
    dead_letter_stream_name: DEAD_LETTER_STREAM_NAME,
    status_proxy: STATUS_PROXY,
) -> None:
    while status_proxy.status_ok:
        event: StreamedEvent = await queue.get()
        try:
            event_payload: DownstreamCounterDecrementData = (
                reconstruct_downstream_counter_data_from_stream(event.payload)
            )
        except (KeyError, ValueError):
            await declare_dead_with_retries(
                event_stream_manager,
                config.WORKER,
                (event,),
                stream_name,
                group_name,
                dead_letter_stream_name,
            )
            continue

        # Downstream counters may be too big to materialize all at once
        limit, offset = config.WORKER.DOWNSTREAM_COUNTER_BATCH_SIZE, 0
        exception: Exception | None = None
        async with pool.connection() as conn:
            if not await dedup_insert_event(conn, event.event_id, event.name):
                await event_stream_manager.acknowledge_events(
                    (event,), stream_name, group_name
                )
                continue

            for _attempt in range(1, config.WORKER.MAX_RETRIES + 1):
                try:
                    # temp truthy tuple to enter loop
                    # (hehe the initial list kinda looks like a wink)
                    results: list[tuple[str, int]] = [("", 0)]
                    while results:
                        results: list[tuple[str, int]] = await select_decrement_deltas(
                            conn,
                            event_payload["affected_column_name"],
                            limit,
                            offset,
                            event_payload["affected_table_name"],
                            event_payload["deletion_author_event_id"],
                        )
                        offset += limit

                        emission_coroutine = (
                            lambda: emit_downstream_counter_decrement_updates(
                                redis,
                                results,
                                event_payload["hashmap_name"],
                                event_payload["affected_table_name"],
                            )
                        )
                        await execute_with_redis_retries(
                            config.WORKER, emission_coroutine
                        )
                except POTENTIAL_TRANSIENT_ERRORS as e:
                    exception = e
                    await conn.rollback()
                    await exponential_jittered_backoff(
                        config.WORKER.MAXIMUM_BACKOFF_INTERVAL,
                        config.WORKER.BASE_BACKOFF_INTERVAL,
                        _attempt,
                        exponential=config.WORKER.BACKOFF_EXPONENTIAL,
                    )
                    continue
                except Exception as e:
                    exception = e
                    await conn.rollback()
                    break

        if exception:
            await declare_dead_with_retries(
                event_stream_manager,
                config.WORKER,
                (event,),
                stream_name,
                group_name,
                dead_letter_stream_name,
            )
        else:
            await ack_with_retries(
                event_stream_manager,
                config.WORKER,
                (event,),
                stream_name,
                group_name,
                dead_letter_stream_name,
            )
