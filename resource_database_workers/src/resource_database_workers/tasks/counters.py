import asyncio
import itertools
from typing import Literal

from redis.asyncio import Redis

from psycopg_pool import AsyncConnectionPool
from resource_auxillary.strings import StreamName

from resource_database_workers.config.config import AppConfig
from resource_database_workers.datastructures.exceptions import (
    RecoverableDatabaseException,
)
from resource_database_workers.utils.worker_redis import (
    declare_counters_event_dead,
    retrieve_counter_group_names,
)
from resource_database_workers.utils.coordination import locked_operation
from resource_database_workers.utils.strings import (
    derive_lock_key,
    extract_batch_metadata,
)
from resource_database_workers.utils.tasks import (
    flush_counter_updates,
    dispatch_to_retrier,
)

# TODO: Add server redis namespace update after succesful processing of counter deltas


async def batch_update_retry_counters(
    config: AppConfig,
    pool: AsyncConnectionPool,
    dlq_stream_name: StreamName,
    worker_redis: Redis,
    server_redis: Redis,
) -> None:
    while True:
        batch_name: str = await worker_redis.blpop(config.WORKER.COUNTER_RETRY_REGISTRY_NAME)  # type: ignore
        if not batch_name:
            await asyncio.sleep(config.WORKER.COUNTER_FLUSH_INTERVAL)
            continue
        await batch_update_counter_group(
            config, pool, batch_name, dlq_stream_name, worker_redis
        )


async def batch_update_counters(
    config: AppConfig,
    pool: AsyncConnectionPool,
    dlq_stream_name: StreamName,
    worker_redis: Redis,
    server_redis: Redis,
) -> None:
    counter_groups: set[str] = await retrieve_counter_group_names(
        worker_redis, config.WORKER.COUNTER_REGISTRY_NAME
    )
    refresh_counter: int = 100  # TODO: Update AppConfig to hold refresh value
    for counter_group in itertools.cycle(counter_groups):
        # Periodically refresh counter group names
        # in the extremely rare case of a schema change
        if refresh_counter <= 0:
            counter_groups: set[str] = await retrieve_counter_group_names(
                worker_redis, config.WORKER.COUNTER_REGISTRY_NAME
            )
            refresh_counter = 100
        await batch_update_counter_group(
            config, pool, counter_group, dlq_stream_name, worker_redis
        )

        refresh_counter -= 1


async def batch_update_counter_group(
    config: AppConfig,
    pool: AsyncConnectionPool,
    batch_name: str,
    dlq_stream_name: StreamName,
    worker_redis: Redis,
) -> None:
    # Acquire lock for processing this counter group
    lock_name: str = derive_lock_key(batch_name)
    lock_set: None | Literal[True] = await worker_redis.set(
        lock_name, 1, ex=config.WORKER.COUNTER_FLUSH_LOCK_TTL, nx=True
    )
    if not lock_set:
        await asyncio.sleep(config.WORKER.IQ_CONSUMER_GET_TIMEOUT)
        return

    async with locked_operation(worker_redis, lock_name):
        async with worker_redis.pipeline(transaction=True) as pipeline:
            pipeline.hgetall(batch_name)
            pipeline.delete(batch_name)
            res = await pipeline.execute()

        if not res[0]:  # hgetall result
            return

        # Cast back to PK:delta key-value pairs
        counters: dict[int, int] = {int(k): int(v) for k, v in res[0].items()}
        del res

        async with pool.connection() as conn:
            try:
                await flush_counter_updates(conn, batch_name, counters)
            except RecoverableDatabaseException:
                group_name, identifier, group_version = extract_batch_metadata(
                    batch_name
                )
                if group_version >= config.WORKER.MAX_RETRIES:
                    await declare_counters_event_dead(
                        worker_redis,
                        dlq_stream_name,
                        batch_name,
                        counters,
                        config.WORKER.MAX_RETRIES,
                    )
                else:
                    await dispatch_to_retrier(
                        config,
                        worker_redis,
                        group_name,
                        counters,
                        current_retry_count=group_version,
                        identifier=identifier,
                    )
            except Exception:
                await declare_counters_event_dead(
                    worker_redis,
                    dlq_stream_name,
                    batch_name,
                    counters,
                    config.WORKER.MAX_RETRIES,
                )
