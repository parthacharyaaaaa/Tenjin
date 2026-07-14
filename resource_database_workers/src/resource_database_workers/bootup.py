import asyncio
from functools import partial
from typing import Any, Callable

from psycopg_pool import AsyncConnectionPool

from redis.asyncio import Redis
from resource_auxillary.strings import StreamName

from resource_database_workers.config.config import AppConfig
from resource_database_workers.config.worker_config import WorkerSettings
from resource_database_workers.datastructures.queues import QueueRegistry
from resource_database_workers.datastructures.worker_inputs import (
    WORKER_INPUT_DATA_MAPPING,
    UpstreamDispatcherInput,
)
from resource_database_workers.datastructures.processors import (
    EVENT_WORKER_MAPPING,
)
from resource_database_workers.datastructures.streams import (
    STREAM_CONSUMER_MAPPING,
    STREAM_EVENT_MAPPING,
)
from resource_database_workers.tasks.dlq_workers import (
    dlq_consumer,
    counters_dlq_consumer,
)
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
        for _ in range(worker_settings.DLQ.STANDARD):
            tg.create_task(dlq_consumer(pg_connection_pool, queue_registry.dead_letter))
        for _ in range(worker_settings.DLQ.COUNTERS):
            tg.create_task(
                counters_dlq_consumer(
                    pg_connection_pool, queue_registry.counter_dead_letter
                )
            )

        # Counters
        for _ in range(worker_settings.COUNTERS.WORKERS):
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

        # Readers
        for (
            stream,
            consumer_count,
        ) in worker_settings.READER.reader_count_mapping.items():
            consumer_task: Callable = STREAM_CONSUMER_MAPPING[stream]

            if stream == StreamName.DOWNSTREAM_COUNTER_DECREMENTS:
                queue_mapping = queue_registry.downstream_decrement_event_queue_mapping
            elif stream == StreamName.DOWNSTREAM_DELETIONS:
                queue_mapping = queue_registry.downstream_deletion_event_queue_apping
            else:
                queue_mapping = queue_registry.event_queue_mapping

            consumer_task_input: UpstreamDispatcherInput = UpstreamDispatcherInput(
                stream_name=stream, queue_mapping=queue_mapping
            )

            for _ in range(consumer_count):
                tg.create_task(
                    partial(consumer_task, consumer_task_input.__dataclass_fields__)()
                )

        # Workers
        for event, worker_count in (
            worker_settings.DOWNSTREAM_DECREMENT.worker_count_mapping
            | worker_settings.DOWNSTREAM_DELETION.worker_count_mapping
            | worker_settings.UPSTREAM.worker_count_mapping
        ).items():
            worker_task: Callable = EVENT_WORKER_MAPPING[event]
            worker_input: Any = WORKER_INPUT_DATA_MAPPING[event]

            for _ in range(worker_count):
                tg.create_task(
                    partial(worker_task, worker_input.__dataclass_fields__)()
                )
