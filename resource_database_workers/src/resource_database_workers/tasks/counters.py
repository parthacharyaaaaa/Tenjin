import asyncio
from typing import Literal

from redis.asyncio import Redis

from psycopg import AsyncConnection
from resource_auxillary.strings import NAME_SEPERATOR

from resource_database_workers.config.config import AppConfig
from resource_database_workers.datastructures.dead_counter_batch import DeadCounterBatch
from resource_database_workers.datastructures.exceptions import (
    RecoverableDatabaseException,
    UnrecoverableDatabaseException,
)
from resource_database_workers.utils.coordination import locked_operation
from resource_database_workers.utils.strings import (
    derive_lock_key,
    derive_counter_group_from_batch,
    derive_version_from_batch,
)
from resource_database_workers.utils.tasks import (
    flush_counter_updates,
    dispatch_to_retrier,
)


async def _dispatch_to_dlq(
    queue: asyncio.Queue[DeadCounterBatch], counter_group: str, batch: dict[int, int]
) -> None:
    table, column = counter_group.split(NAME_SEPERATOR)
    dlq_batch: DeadCounterBatch = DeadCounterBatch(table, column, batch)
    await queue.put(dlq_batch)


async def batch_update_counters(
    config: AppConfig,
    conn: AsyncConnection,
    dead_letter_queue: asyncio.Queue[DeadCounterBatch],
    worker_redis: Redis,
    server_redis: Redis,
) -> None:
    counter_groups: set[str] = {
        str(i)
        for i in (
            await worker_redis.smembers(config.WORKER.COUNTER_REGISTRY_NAME)  # type: ignore[reportGeneralTypeIssues]
        )
    }
    for counter_group in counter_groups:
        lock_name: str = derive_lock_key(counter_group)
        lock_set: None | Literal[True] = await worker_redis.set(
            lock_name, 1, ex=config.WORKER.COUNTER_FLUSH_LOCK_TTL, nx=True
        )
        if not lock_set:
            continue

        async with locked_operation(worker_redis, lock_name):
            async with worker_redis.pipeline(transaction=True) as pipeline:
                pipeline.hgetall(counter_group)
                pipeline.delete(counter_group)
                res = await pipeline.execute()

            if not res[0]:
                continue
            counters: dict[int, int] = {int(k): int(v) for k, v in res[0].items()}
            try:
                await flush_counter_updates(conn, counter_group, counters)
            except RecoverableDatabaseException:
                await conn.rollback()
                await dispatch_to_retrier(config, worker_redis, counter_group, counters)
            except UnrecoverableDatabaseException:
                await conn.rollback()
                await _dispatch_to_dlq(dead_letter_queue, counter_group, counters)
            except Exception:
                await conn.rollback()
                await _dispatch_to_dlq(dead_letter_queue, counter_group, counters)


async def retry_batch_update_counters(
    config: AppConfig,
    conn: AsyncConnection,
    dead_letter_queue: asyncio.Queue[DeadCounterBatch],
    worker_redis: Redis,
    server_redis: Redis,
) -> None:
    while True:
        # Absolutely awful type hinting support for blpop, good god
        batch_name: str = await worker_redis.blpop(config.WORKER.COUNTER_RETRY_REGISTRY_NAME)  # type: ignore
        if not batch_name:
            await asyncio.sleep(config.WORKER.COUNTER_FLUSH_INTERVAL)
            continue

        counter_group = derive_counter_group_from_batch(batch_name)
        lock_name: str = derive_lock_key(counter_group)
        lock_set: None | Literal[True] = await worker_redis.set(
            lock_name, 1, ex=config.WORKER.COUNTER_FLUSH_LOCK_TTL, nx=True
        )

        if not lock_set:
            await worker_redis.rpush(config.WORKER.COUNTER_RETRY_REGISTRY_NAME, batch_name)  # type: ignore[reportGeneralTypeIssues]
            continue

        async with locked_operation(worker_redis, lock_name):
            async with worker_redis.pipeline(transaction=True) as pipeline:
                pipeline.hgetall(batch_name)
                pipeline.delete(batch_name)
                res = await pipeline.execute()

            # Primary key : delta
            counters: dict[int, int] = {int(k): int(v) for k, v in res[0].items()}
            del res
            try:
                await flush_counter_updates(conn, counter_group, counters)
            except RecoverableDatabaseException:
                await conn.rollback()
                version: int = derive_version_from_batch(batch_name)
                if version >= config.WORKER.MAX_RETRIES:
                    await _dispatch_to_dlq(dead_letter_queue, counter_group, counters)
                else:
                    await dispatch_to_retrier(
                        config,
                        worker_redis,
                        counter_group,
                        counters,
                        current_retry_count=version,
                    )
            except UnrecoverableDatabaseException:
                await conn.rollback()
                await _dispatch_to_dlq(dead_letter_queue, counter_group, counters)
            except Exception:
                await conn.rollback()
                await _dispatch_to_dlq(dead_letter_queue, counter_group, counters)
