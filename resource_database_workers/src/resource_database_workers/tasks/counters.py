import asyncio
import itertools
from typing import Literal, MutableMapping

from redis.asyncio import Redis

from psycopg_pool import AsyncConnectionPool
from resource_auxillary.strings import NAME_SEPERATOR, StreamName

from resource_database_workers.config.config import AppConfig
from resource_database_workers.datastructures.exceptions import (
    RecoverableDatabaseException,
)
from resource_database_workers.utils.worker_redis import (
    declare_counters_event_dead,
    reflect_processed_counters,
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
        counter_data: dict[str, int] | None = await batch_update_counter_group(
            config, pool, batch_name, dlq_stream_name, worker_redis
        )
        if not counter_data:
            continue

        await reflect_processed_counters(
            server_redis, extract_batch_metadata(batch_name)[0], counter_data
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

        counter_data: dict[str, int] | None = await batch_update_counter_group(
            config, pool, counter_group, dlq_stream_name, worker_redis
        )
        if not counter_data:
            continue

        refresh_counter -= 1

        await reflect_processed_counters(server_redis, counter_group, counter_data)


def _cache_normalize_raw_counter_data(
    raw_counters: MutableMapping[str, str],
) -> dict[str, int]:
    return {k: int(v) for k, v in raw_counters}


def _database_normalize_cache_normalized_counter_data(
    raw_counters: MutableMapping[str, int],
) -> dict[int, int]:
    return {int(k.split(NAME_SEPERATOR)[1]): v for k, v in raw_counters.items()}


async def batch_update_counter_group(
    config: AppConfig,
    pool: AsyncConnectionPool,
    batch_name: str,
    dlq_stream_name: StreamName,
    worker_redis: Redis,
) -> dict[str, int] | None:
    # Acquire lock for processing this counter group
    lock_name: str = derive_lock_key(batch_name)
    lock_set: None | Literal[True] = await worker_redis.set(
        lock_name, 1, ex=config.WORKER.COUNTER_FLUSH_LOCK_TTL, nx=True
    )
    if not lock_set:
        return None

    async with locked_operation(worker_redis, lock_name):
        async with worker_redis.pipeline(transaction=True) as pipeline:
            pipeline.hgetall(batch_name)
            pipeline.delete(batch_name)
            res = await pipeline.execute()

        if not res[0]:  # hgetall result
            return None

        # Cast back to cache_key:delta key-value pairs
        counters: dict[str, int] = _cache_normalize_raw_counter_data(res[0])
        del res

        async with pool.connection() as conn:
            db_normalized_counters: dict[int, int] = (
                _database_normalize_cache_normalized_counter_data(counters)
            )
            try:
                await flush_counter_updates(conn, batch_name, db_normalized_counters)
                return counters
            except RecoverableDatabaseException:
                group_name, identifier, group_version = extract_batch_metadata(
                    batch_name
                )
                if group_version >= config.WORKER.MAX_RETRIES:
                    await declare_counters_event_dead(
                        worker_redis,
                        dlq_stream_name,
                        batch_name,
                        db_normalized_counters,
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
                return None
            except Exception:
                await declare_counters_event_dead(
                    worker_redis,
                    dlq_stream_name,
                    batch_name,
                    db_normalized_counters,
                    config.WORKER.MAX_RETRIES,
                )
                return None
