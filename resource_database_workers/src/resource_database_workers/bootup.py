import asyncio
from functools import partial
from typing import Any, Callable

from psycopg_pool import AsyncConnectionPool

from redis.asyncio import Redis

from resource_database_workers.config.config import AppConfig
from resource_database_workers.config.worker_config import WorkerSettings
from resource_database_workers.datastructures.queues import QueueRegistry
from resource_database_workers.datastructures.worker_inputs import (
    WORKER_INPUT_DATA_MAPPING,
)
from resource_database_workers.src.resource_database_workers.datastructures.processors import (
    EVENT_WORKER_MAPPING,
)
from resource_database_workers.tasks.consumer import dlq_consumer, counters_dlq_consumer
from resource_database_workers.tasks.counters import (
    batch_update_counters,
    retry_batch_update_counters,
)


async def spawn_tasks(
    app_config: AppConfig,
    worker_settings: WorkerSettings,
    app_redis: Redis,
    internal_redis: Redis,
    queue_registry: QueueRegistry,
    pg_connection_pool: AsyncConnectionPool,
) -> None:
    async with asyncio.TaskGroup() as tg:
        # DLQ
        for _ in range(worker_settings.standard_dlq):
            tg.create_task(dlq_consumer(pg_connection_pool, queue_registry.dead_letter))
        for _ in range(worker_settings.counters_dlq):
            tg.create_task(
                counters_dlq_consumer(
                    pg_connection_pool, queue_registry.counter_dead_letter
                )
            )

        # Counters
        for _ in range(worker_settings.counters):
            tg.create_task(
                batch_update_counters(
                    app_config,
                    pg_connection_pool,
                    queue_registry.counter_dead_letter,
                    internal_redis,
                    app_redis,
                )
            )
            tg.create_task(
                retry_batch_update_counters(
                    app_config,
                    pg_connection_pool,
                    queue_registry.counter_dead_letter,
                    internal_redis,
                    app_redis,
                )
            )

        for event, consumer_count in worker_settings.queue_worker_counts.items():
            inputs: Any = WORKER_INPUT_DATA_MAPPING[event]
            worker: Callable = EVENT_WORKER_MAPPING[event]
            for _ in range(consumer_count):
                tg.create_task(partial(worker, inputs)())
