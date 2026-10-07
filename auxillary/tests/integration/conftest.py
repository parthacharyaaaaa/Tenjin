import pytest
import pytest_asyncio
from redis.asyncio.client import Redis
from testcontainers.community import redis


@pytest.fixture(scope="package")
def redis_client_container():
    with redis.AsyncRedisContainer(image="redis:7-alpine") as container:
        yield container


@pytest_asyncio.fixture
async def async_redis_client(
    redis_client_container: redis.AsyncRedisContainer,
) -> Redis:
    return await redis_client_container.get_async_client()
