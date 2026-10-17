from datetime import timedelta
from types import SimpleNamespace
from typing import Final
from uuid import uuid4

import pytest
import pytest_asyncio
from auth_server.config import AdminConfigModel
from auth_server.dependencies.local import KeydataRepository
from auth_server.models.database import Base
from auth_server.repositories.admin import AdminRepository
from auth_server.repositories.suspicious_activity import SuspiciousActivityRepository
from auth_server.subsystems.key_manager import SyncedStoreKeyStateManager
from auth_server.subsystems.session_manager import AdminSessionManager
from auth_server.subsystems.token_manager import TokenManager
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import PrivateFormat, PublicFormat
from redis.asyncio.client import Redis
from sqlalchemy.engine import URL
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community import redis
from testcontainers.community.postgres import PostgresContainer


### CONFIGURATIONS ###
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
def dummy_token_manager_config() -> SimpleNamespace:
    return SimpleNamespace(
        REFRESH_LIFETIME=timedelta(days=30),
        ACCESS_LIFETIME=timedelta(minutes=15),
        LEEWAY=timedelta(seconds=30),
        ANNOUNCEMENT_DURATION=timedelta(minutes=5),
        ALG="ES256",
        MAX_TOKENS_PER_FAMILY=5,
        POLL_INTERVAL=timedelta(seconds=60),
    )


@pytest.fixture(scope="package")
def dummy_key_config() -> SimpleNamespace:
    return SimpleNamespace(
        MAX_VALID_KEYS=3,
        KEY_ROTATION_COOLDOWN=timedelta(hours=1),
        KEY_IDENTIFIER_LENGTH=16,
        SIGNATURE_ALGORITHM="ES256",
        EC_TYPE=ec.SECP256R1(),
        PRIVATE_PEM_FORMAT=PrivateFormat.PKCS8,
        PUBLIC_PEM_FORMAT=PublicFormat.SubjectPublicKeyInfo,
    )


@pytest.fixture(scope="package")
def dummy_admin_config() -> AdminConfigModel:
    return AdminConfigModel(
        SUSPICIOUS_LOOKBACK_TIME=3,
        MAX_ACTIVITY_LIMIT=3,
        MAX_SESSION_ITERATIONS=3,
        ADMIN_SESSION_DURATION=10,
        REVIVAL_DIGEST_LENGTH=32,
    )


### END ###


### POSTGRES ###
@pytest.fixture(scope="package")
def postgres_client_container():
    with PostgresContainer(image="postgres:17.9") as postgres_container:
        yield postgres_container


@pytest_asyncio.fixture(scope="module")
async def pg_async_sessionmaker(postgres_client_container: PostgresContainer):
    base_psql_url: Final[URL] = make_url(
        postgres_client_container.get_connection_url(driver="psycopg")
    )
    test_database_name: Final[str] = f"test_{uuid4().hex}"
    test_db_url: Final[URL] = base_psql_url.set(database=test_database_name)
    admin_db_url: Final[URL] = base_psql_url.set(database="postgres")
    admin_engine: Final[AsyncEngine] = create_async_engine(admin_db_url)
    engine: AsyncEngine | None = None

    try:
        async with admin_engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.exec_driver_sql(f'CREATE DATABASE "{test_database_name}"')

        engine = create_async_engine(test_db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_maker: Final[async_sessionmaker[AsyncSession]] = async_sessionmaker(
            bind=engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=True,
        )
        yield session_maker
    finally:
        if engine is not None:
            await engine.dispose()
        async with admin_engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.exec_driver_sql(
                f'DROP DATABASE IF EXISTS "{test_database_name}" WITH (FORCE)'
            )
        await admin_engine.dispose()


### END ###


### REPOSITORIES ###
@pytest.fixture(scope="module")
def keydata_repository(pg_async_sessionmaker: async_sessionmaker[AsyncSession]):
    return KeydataRepository(pg_async_sessionmaker)


@pytest.fixture(scope="module")
def suspicious_activity_repository(
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
):
    return SuspiciousActivityRepository(pg_async_sessionmaker)


@pytest.fixture(scope="module")
def admin_repository(
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
):
    return AdminRepository(pg_async_sessionmaker)


### END ###


### REDIS ###
@pytest.fixture(scope="package")
def redis_client_container():
    with redis.AsyncRedisContainer(image="redis:7-alpine") as container:
        yield container


@pytest.fixture(scope="package")
def token_store_container():
    with redis.AsyncRedisContainer(image="redis:7-alpine") as container:
        yield container


@pytest_asyncio.fixture(scope="package", loop_scope="package")
async def async_redis_client(
    redis_client_container: redis.AsyncRedisContainer,
):
    try:
        client = await redis_client_container.get_async_client(decode_responses=True)
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture(scope="package", loop_scope="package")
async def token_store_client(
    token_store_container: redis.AsyncRedisContainer,
):
    try:
        client = await token_store_container.get_async_client(decode_responses=True)
        yield client
    finally:
        await client.aclose()


### END ###


### APPLICATION ###
@pytest.fixture(scope="package")
def admin_session_manager(
    async_redis_client: Redis, dummy_admin_config: AdminConfigModel
) -> AdminSessionManager:
    return AdminSessionManager(async_redis_client, dummy_admin_config)


@pytest.fixture(scope="package")
def synced_key_store_manager(async_redis_client: Redis) -> SyncedStoreKeyStateManager:
    return SyncedStoreKeyStateManager(async_redis_client)


@pytest.fixture(scope="package")
def token_manager(
    token_store_client: Redis,
    async_redis_client: Redis,
    keydata_repository: KeydataRepository,
    dummy_key_config: SimpleNamespace,
    dummy_token_manager_config: SimpleNamespace,
) -> TokenManager:
    return TokenManager(
        token_store_client,
        async_redis_client,
        keydata_repository,
        dummy_key_config,  # type: ignore
        dummy_token_manager_config,  # type: ignore
    )


### END ###
