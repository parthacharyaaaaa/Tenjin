from types import SimpleNamespace
from typing import Final

import pytest
import pytest_asyncio
from auth_server.config import AdminConfigModel
from auth_server.dependencies.local import KeydataRepository
from auth_server.models.database import Base
from auth_server.repositories.admin import AdminRepository
from auth_server.repositories.suspicious_activity import SuspiciousActivityRepository
from auth_server.subsystems.key_manager import SyncedStoreKeyStateManager
from auth_server.subsystems.session_manager import AdminSessionManager
from redis.asyncio.client import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community import redis
from testcontainers.community.postgres import PostgresContainer


@pytest.fixture(scope="package")
def dummy_sqlalchemy_config() -> SimpleNamespace:
    return SimpleNamespace(
        SQLALCHEMY_POOL_SIZE=1,
        SQLALCHEMY_MAX_OVERFLOW=0,
        SQLALCHEMY_POOL_RECYCLE=0,
        SQLALCHEMY_POOL_TIMEOUT=8,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )


@pytest.fixture(scope="package")
def postgres_client_container():
    with PostgresContainer(image="postgres:17.9") as postgres_container:
        yield postgres_container


@pytest_asyncio.fixture(scope="package")
async def pg_async_sessionmaker(postgres_client_container: PostgresContainer):
    psql_url = postgres_client_container.get_connection_url(driver="psycopg")
    engine: Final[AsyncEngine] = create_async_engine(psql_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        session_maker: Final[async_sessionmaker[AsyncSession]] = async_sessionmaker(
            bind=engine, autocommit=False, autoflush=False, expire_on_commit=True
        )
        yield session_maker
    finally:
        await engine.dispose()


@pytest.fixture
def keydata_repository(pg_async_sessionmaker: async_sessionmaker[AsyncSession]):
    return KeydataRepository(pg_async_sessionmaker)


@pytest.fixture
def suspicious_activity_repository(
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
):
    return SuspiciousActivityRepository(pg_async_sessionmaker)


@pytest.fixture
def admin_repository(
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
):
    return AdminRepository(pg_async_sessionmaker)


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
