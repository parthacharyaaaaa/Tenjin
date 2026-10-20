from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Final
from uuid import uuid4

import pytest
import pytest_asyncio
from auth_server.admin.roles import AdminRole
from auth_server.config import AdminConfigModel
from auth_server.config.sub_config import JWKSConfigModel
from auth_server.dependencies.local import KeydataRepository
from auth_server.models.database import Admin, Base, KeyData
from auth_server.repositories.admin import AdminRepository
from auth_server.repositories.suspicious_activity import SuspiciousActivityRepository
from auth_server.subsystems.jwks_announcer import JWKSUpdateAnnouncer
from auth_server.subsystems.key_manager import (
    KeyLifecycleManager,
    SyncedStoreKeyStateManager,
)
from auth_server.subsystems.session_manager import AdminSessionManager
from auth_server.subsystems.token_manager import TokenManager
from auxillary.data_structures.locks.lock import RedisInstanceLockFactory
from auxillary.mixins.db_config import BasicPostgresDatabaseConfigMixin
from auxillary.security.data_structures.jwks.enums import ECAlg
from auxillary.security.serialization import (
    pem_serialize_private_key,
    pem_serialize_public_key,
)
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import (
    SECP256R1,
    EllipticCurvePrivateKey,
    derive_private_key,
)
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
from sqlalchemy.sql.expression import delete, insert, select
from testcontainers.community import redis
from testcontainers.community.postgres import PostgresContainer

### CONFIGURATIONS ###
# @pytest.fixture(scope="package")
# def dummy_sqlalchemy_config() -> SimpleNamespace:
#     return SimpleNamespace(
#         SQLALCHEMY_POOL_SIZE=1,
#         SQLALCHEMY_MAX_OVERFLOW=0,
#         SQLALCHEMY_POOL_RECYCLE=0,
#         SQLALCHEMY_POOL_TIMEOUT=8,
#         SQLALCHEMY_TRACK_MODIFICATIONS=False,
#     )


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


@pytest.fixture(scope="package")
def dummy_jwks_config(dummy_token_manager_config: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        JWKS_CAP=3,
        UPDATION_STREAM_NAME="jwks_updation_stream",
        TOKEN_MANAGER=dummy_token_manager_config,
    )


@pytest.fixture(scope="package")
def dummy_bootup_config() -> SimpleNamespace:
    return SimpleNamespace(
        SLAVE_SLEEP_POLLING_INTERVAL=timedelta(seconds=1),
        MASTER_BOOTUP_LOCK_LIFESPAN=timedelta(seconds=1),
    )


@pytest.fixture(scope="package")
def dummy_sqlalchemy_config() -> SimpleNamespace:
    return SimpleNamespace(
        SQLALCHEMY_DATABASE_URI_TEMPLATE=(
            "postgresql+psycopg://{username}:{password}@{host}:{port}/{database}"
        ),
        SQLALCHEMY_POOL_SIZE=4,
        SQLALCHEMY_MAX_OVERFLOW=4,
        SQLALCHEMY_POOL_RECYCLE=60,
        SQLALCHEMY_POOL_TIMEOUT=10,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )


class DummyPostgresConfig(BasicPostgresDatabaseConfigMixin):
    def __init__(self, host, port, database) -> None:
        self.POSTGRES_HOST = host
        self.POSTGRES_PORT = port
        self.POSTGRES_DATABASE = database


@pytest.fixture(scope="package")
def dummy_database_config(
    postgres_client_container: PostgresContainer,
) -> DummyPostgresConfig:
    return DummyPostgresConfig(
        host=postgres_client_container.get_container_host_ip(),
        port=postgres_client_container.port,
        database=postgres_client_container.dbname,
    )


@pytest.fixture(scope="package")
def dummy_core_config() -> SimpleNamespace:
    return SimpleNamespace(APPLICATION_ROOT="")


@pytest.fixture(scope="package")
def dummy_app_config(
    dummy_core_config: SimpleNamespace,
    dummy_jwks_config: SimpleNamespace,
    dummy_key_config: SimpleNamespace,
    dummy_admin_config: SimpleNamespace,
    dummy_bootup_config: SimpleNamespace,
    dummy_database_config: DummyPostgresConfig,
) -> SimpleNamespace:
    return SimpleNamespace(
        CORE=dummy_core_config,
        JWKS=dummy_jwks_config,
        KEYS=dummy_key_config,
        ADMIN=dummy_admin_config,
        BOOTUP=dummy_bootup_config,
        DATABASE=dummy_database_config,
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
def redis_lock_factory(async_redis_client: Redis) -> RedisInstanceLockFactory:
    return RedisInstanceLockFactory(async_redis_client)


@pytest.fixture(scope="package")
def admin_session_manager(
    async_redis_client: Redis, dummy_admin_config: AdminConfigModel
) -> AdminSessionManager:
    return AdminSessionManager(async_redis_client, dummy_admin_config)


@pytest.fixture(scope="package")
def synced_key_store_manager(async_redis_client: Redis) -> SyncedStoreKeyStateManager:
    return SyncedStoreKeyStateManager(async_redis_client)


@pytest.fixture(scope="module")
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


@pytest.fixture(scope="package")
def jwks_update_announcer(
    async_redis_client: Redis, dummy_jwks_config: SimpleNamespace
) -> JWKSUpdateAnnouncer:
    return JWKSUpdateAnnouncer(
        async_redis_client,
        dummy_jwks_config,  # type: ignore
    )


@pytest.fixture(scope="module")
def key_lifecycle_manager(
    keydata_repository: KeydataRepository,
    token_manager: TokenManager,
    dummy_key_config: SimpleNamespace,
    synced_key_store_manager: SyncedStoreKeyStateManager,
    redis_lock_factory: RedisInstanceLockFactory,
    jwks_update_announcer: JWKSUpdateAnnouncer,
    dummy_jwks_config: SimpleNamespace,
) -> KeyLifecycleManager:
    return KeyLifecycleManager(
        keydata_repository,
        token_manager,
        dummy_key_config,  # type: ignore
        dummy_jwks_config,  # type: ignore
        synced_key_store_manager,
        redis_lock_factory,
        jwks_update_announcer,
    )


### END ###


### RUNTIME FUNCTIONALITY ###
@pytest.fixture(scope="package")
def test_superuser_admin_name() -> str:
    return "TEST_ADMIN"


@pytest.fixture(scope="package")
def test_superuser_admin_id() -> int:
    return 1


@pytest.fixture(scope="package")
def test_superuser_admin_password_hash() -> bytes:
    return b"x" * 32


@pytest_asyncio.fixture(scope="function")
async def prepare_database_state(
    test_superuser_admin_name: str,
    test_superuser_admin_id: int,
    test_superuser_admin_password_hash: bytes,
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
    keydata_repository: KeydataRepository,
    dummy_jwks_config: JWKSConfigModel,
) -> None:
    async with pg_async_sessionmaker() as session:
        super_admin = (
            await session.execute(
                select(Admin).where(Admin.id_ == test_superuser_admin_id)
            )
        ).scalar_one_or_none()
        if not super_admin:
            await session.execute(
                insert(Admin).values(
                    id_=test_superuser_admin_id,
                    username=test_superuser_admin_name,
                    role=AdminRole.SUPER,
                    password_hash=test_superuser_admin_password_hash,
                    created_by=test_superuser_admin_id,
                )
            )
        await session.execute(delete(KeyData))
        await session.commit()

    async with keydata_repository.unit_of_work():
        dummy_key: EllipticCurvePrivateKey = derive_private_key(1, SECP256R1())
        await keydata_repository.insert_keydata(
            "1",
            pem_serialize_private_key(dummy_key, PrivateFormat.PKCS8),
            pem_serialize_public_key(
                dummy_key.public_key(), PublicFormat.SubjectPublicKeyInfo
            ),
            ECAlg.ES256,
            SECP256R1(),
            epoch=datetime(2026, 1, 1, tzinfo=UTC),
        )
        for i in range(2, dummy_jwks_config.JWKS_CAP * 2):
            dummy_key: EllipticCurvePrivateKey = derive_private_key(i, SECP256R1())
            await keydata_repository.rotate_key(
                str(i - 1),
                str(i),
                pem_serialize_public_key(
                    dummy_key.public_key(), PublicFormat.SubjectPublicKeyInfo
                ),
                pem_serialize_private_key(dummy_key, PrivateFormat.PKCS8),
                alg=ECAlg.ES256,
                curve=SECP256R1,
                epoch=datetime(2026, 1, i, tzinfo=UTC),
            )
            if i > dummy_jwks_config.JWKS_CAP:
                await keydata_repository.expire_keydata(
                    str(i - dummy_jwks_config.JWKS_CAP),
                    expiry_time=datetime(2026, 1, i, tzinfo=UTC),
                )


@pytest_asyncio.fixture
async def clean_synced_store(async_redis_client: Redis) -> None:
    await async_redis_client.flushall()
