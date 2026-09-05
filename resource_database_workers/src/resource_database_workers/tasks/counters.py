from resource_auxillary.event_processing.event_stream_manager import EventStreamManager
from resource_database_workers.dependencies.annotations import EVENT_STREAM_MANAGER
from resource_database_workers.dependencies.annotations import STATUS_PROXY
from resource_database_workers.dependencies.annotations import APP_REDIS
from resource_database_workers.dependencies.annotations import INTERNAL_REDIS
from resource_database_workers.dependencies.annotations import DEAD_LETTER_STREAM_NAME
from resource_database_workers.dependencies.annotations import CONNECTION_POOL
from resource_database_workers.dependencies.annotations import APP_CONFIG
import asyncio
import time
from typing import Literal, MutableMapping

from redis.asyncio import Redis

from psycopg_pool import AsyncConnectionPool

from resource_auxillary.event_processing.qos import locked_operation
from resource_auxillary.strings import NAME_SEPERATOR, StreamName

from resource_database_workers.config.config import AppConfig
from resource_database_workers.datastructures.exceptions import (
    RecoverableDatabaseException,
)
from resource_database_workers.workers.redis.declarations import (
    declare_counters_event_dead,
)

from resource_database_workers.workers.redis.cache import (
    reflect_processed_counters,
)
from resource_database_workers.workers.redis.counters import (
    retrieve_counter_group_names,
    dispatch_to_retrier,
)
from resource_database_workers.workers.database.counters import (
    flush_counter_updates,
)

from resource_database_workers.utils.strings import (
    derive_lock_key,
    extract_batch_metadata,
)


async def batch_update_retry_counters(
    config: APP_CONFIG,
    pool: CONNECTION_POOL,
    event_stream_manager: EVENT_STREAM_MANAGER,
    dlq_stream_name: DEAD_LETTER_STREAM_NAME,
    worker_redis: INTERNAL_REDIS,
    server_redis: APP_REDIS,
    status_proxy: STATUS_PROXY,
) -> None:
    while status_proxy.status_ok:
        batch_name: str = await worker_redis.blpop(config.WORKER.COUNTER_RETRY_REGISTRY_NAME)  # type: ignore
        if not batch_name:
            await asyncio.sleep(config.WORKER.COUNTER_FLUSH_INTERVAL)
            continue
        counter_data: dict[str, int] | None = await batch_update_counter_group(
            config,
            pool,
            event_stream_manager,
            batch_name,
            dlq_stream_name,
            worker_redis,
        )
        if not counter_data:
            continue

        await reflect_processed_counters(
            server_redis, extract_batch_metadata(batch_name)[0], counter_data
        )


async def batch_update_counters(
    config: APP_CONFIG,
    pool: CONNECTION_POOL,
    event_stream_manager: EVENT_STREAM_MANAGER,
    dlq_stream_name: DEAD_LETTER_STREAM_NAME,
    worker_redis: INTERNAL_REDIS,
    server_redis: APP_REDIS,
    status_proxy: STATUS_PROXY,
) -> None:
    counter_groups: list[str] = list(
        await retrieve_counter_group_names(
            worker_redis, config.WORKER.COUNTER_REGISTRY_NAME
        )
    )
    refresh_time: int = int(time.monotonic())
    counter_group_iterator_index: int = 0
    while status_proxy.status_ok:
        # Periodically refresh counter group names
        # in the extremely rare case of a schema change
        if (
            int(time.monotonic()) - refresh_time
            >= config.WORKER.COUNTER_REGISTRY_REFRESH_INTERVAL
        ):
            counter_groups = list(
                await retrieve_counter_group_names(
                    worker_redis, config.WORKER.COUNTER_REGISTRY_NAME
                )
            )
            refresh_time = int(time.monotonic())

        counter_data: dict[str, int] | None = await batch_update_counter_group(
            config,
            pool,
            event_stream_manager,
            counter_groups[counter_group_iterator_index],
            dlq_stream_name,
            worker_redis,
        )

        if not counter_data:
            counter_group_iterator_index = (counter_group_iterator_index + 1) % len(
                counter_groups
            )
            continue

        await reflect_processed_counters(
            server_redis, counter_groups[counter_group_iterator_index], counter_data
        )
        counter_group_iterator_index = (counter_group_iterator_index + 1) % len(
            counter_groups
        )


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
    event_stream_manager: EventStreamManager,
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
                        event_stream_manager,
                        config.WORKER,
                        dlq_stream_name,
                        batch_name,
                        db_normalized_counters,
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
                    event_stream_manager,
                    config.WORKER,
                    dlq_stream_name,
                    batch_name,
                    db_normalized_counters,
                )
                return None
