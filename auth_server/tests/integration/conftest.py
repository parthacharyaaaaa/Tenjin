import pytest
import pytest_asyncio
from auth_server.config import AdminConfigModel
from auth_server.subsystems.key_manager import SyncedStoreKeyStateManager
from auth_server.subsystems.session_manager import AdminSessionManager
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
    return await redis_client_container.get_async_client(decode_responses=True)


@pytest.fixture
def dummy_admin_config() -> AdminConfigModel:
    return AdminConfigModel(
        SUSPICIOUS_LOOKBACK_TIME=3,
        MAX_ACTIVITY_LIMIT=3,
        MAX_SESSION_ITERATIONS=3,
        ADMIN_SESSION_DURATION=10,
        REVIVAL_DIGEST_LENGTH=32,
    )


@pytest.fixture
def admin_session_manager(
    async_redis_client: Redis, dummy_admin_config: AdminConfigModel
) -> AdminSessionManager:
    return AdminSessionManager(async_redis_client, dummy_admin_config)


@pytest.fixture
def synced_key_store_manager(async_redis_client: Redis) -> SyncedStoreKeyStateManager:
    return SyncedStoreKeyStateManager(async_redis_client)
