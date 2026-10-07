from typing import Final

import pytest
from auxillary.data_structures.locks.lock import (
    RedisInstanceLockFactory,
)
from redis.asyncio.client import Redis


@pytest.mark.asyncio
async def test_locking_concurrency(async_redis_client: Redis) -> None:
    lock_factory = RedisInstanceLockFactory(async_redis_client)
    resource: Final[str] = "foo"
    value: Final[str] = "bar"
    ttl: Final[int] = 10

    async with await lock_factory.lock(resource, value, ttl):
        invalid_lock_context = await lock_factory.lock(resource, value, ttl)
    assert not invalid_lock_context.valid, (
        "Lock on existing resource lock found to be valid"
    )
