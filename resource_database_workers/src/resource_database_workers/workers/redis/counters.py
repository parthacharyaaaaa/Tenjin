"""Utilities for counter workers"""

from redis.asyncio import Redis

from resource_database_workers.src.resource_database_workers.config.config import (
    AppConfig,
)
from resource_database_workers.src.resource_database_workers.utils.strings import (
    generate_retry_batch_name,
)


async def retrieve_counter_group_names(redis: Redis, registry_name: str) -> set[str]:
    return {
        str(i)
        for i in (
            await redis.smembers(registry_name)  # type: ignore[reportGeneralTypeIssues]
        )
    }


async def dispatch_to_retrier(
    config: AppConfig,
    worker_redis: Redis,
    counter_group: str,
    counter_data: dict[str, int],
    *,
    current_retry_count: int = 0,
    identifier: str | None = None,
) -> None:
    batch_name: str = generate_retry_batch_name(
        counter_group, current_retry_count + 1, identifier
    )
    async with worker_redis.pipeline(transaction=True) as pipeline:
        pipeline.rpush(config.WORKER.COUNTER_RETRY_REGISTRY_NAME, batch_name)
        pipeline.hset(batch_name, mapping=counter_data)
        await pipeline.execute()
