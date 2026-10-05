"""Functions interfacing with application cache"""

from typing import Mapping

from redis.asyncio import Redis

from resource_database_workers.utils.lua_commands import (
    CONDITIONAL_COUNTER_DECREMENT_TEMPLATE,
)


async def reflect_processed_counters(
    server_redis: Redis, counter_group: str, counters: Mapping[str, int]
) -> None:
    async with server_redis.pipeline(transaction=True) as pipeline:
        for k, v in counters.items():
            pipeline.eval(
                CONDITIONAL_COUNTER_DECREMENT_TEMPLATE, 2, counter_group, k, v
            )
        await pipeline.execute()
