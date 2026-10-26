from collections.abc import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from redis.asyncio.client import Redis
from testcontainers.community import redis
from testcontainers.community.redis import RedisContainer


@pytest.fixture(scope="package")
def redis_client_container() -> Generator[RedisContainer, None, None]:
    with redis.AsyncRedisContainer(image="redis:7-alpine") as container:
        yield container


@pytest_asyncio.fixture
async def async_redis_client(
    redis_client_container: redis.AsyncRedisContainer,
) -> AsyncGenerator[Redis, None]:
    async with await redis_client_container.get_async_client(
        decode_responses=True
    ) as client:
        yield client
