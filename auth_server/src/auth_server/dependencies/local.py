import os
from functools import lru_cache
from typing import AsyncGenerator, Final

from auxillary.data_structures.enriched.link_builder import HypermediaLinkBuilder
from auxillary.data_structures.locks.lock import RedisInstanceLockFactory
from auxillary.data_structures.uow import MultiRepositoryWorkCoordinator
from fastapi import Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from auth_server.admin.session_manager import AdminSessionManager
from auth_server.config import AppConfig
from auth_server.keys.key_manager import (
    FileSystemKeyManager,
    KeyLifecycleManager,
    SyncedStoreKeyStateManager,
)
from auth_server.repositories.admin import AdminRepository
from auth_server.repositories.keydata import KeydataRepository
from auth_server.repositories.suspicious_activity import SuspiciousActivityRepository
from auth_server.tokens.token_manager import TokenManager


def get_hypermedia_link_builder(request: Request) -> HypermediaLinkBuilder:
    return HypermediaLinkBuilder(request)


@lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    return AppConfig()  # type: ignore[reportCallIssue]


@lru_cache(maxsize=1)
def get_synced_store_client() -> Redis:
    config: Final[AppConfig] = get_app_config()

    return Redis(
        # username=os.environ["AUTH_WORKER_REDIS_USERNAME"],
        # password=os.environ["AUTH_WORKER_REDIS_PASSWORD"],
        **config.REDIS.SYNCED_STORE.model_dump(),
    )


@lru_cache(maxsize=1)
def get_token_store_client() -> Redis:
    config: Final[AppConfig] = get_app_config()

    return Redis(
        # username=os.environ["AUTH_WORKER_REDIS_USERNAME"],
        # password=os.environ["AUTH_WORKER_REDIS_PASSWORD"],
        **config.REDIS.TOKEN_STORE.model_dump(),
    )


@lru_cache(maxsize=1)
def get_database_session_maker() -> async_sessionmaker[AsyncSession]:
    config: Final[AppConfig] = get_app_config()

    uri: Final[str] = config.DATABASE.derive_sqlalchemy_uri(
        username=os.environ["AUTH_WORKER_POSTGRES_USERNAME"],
        password=os.environ["AUTH_WORKER_POSTGRES_PASSWORD"],
    )

    engine: Final[AsyncEngine] = create_async_engine(uri)

    session_maker: Final[async_sessionmaker[AsyncSession]] = async_sessionmaker(
        bind=engine, autocommit=False, autoflush=False, expire_on_commit=True
    )

    return session_maker


async def get_database_session() -> AsyncGenerator[AsyncSession, None]:
    session_maker: async_sessionmaker = get_database_session_maker()
    session: Final[AsyncSession] = session_maker()
    try:
        yield session
    finally:
        await session.close()


def get_keydata_repository() -> KeydataRepository:
    return KeydataRepository(get_database_session_maker())


def get_admin_repository() -> AdminRepository:
    return AdminRepository(get_database_session_maker())


def get_suspicious_activity_repository() -> SuspiciousActivityRepository:
    return SuspiciousActivityRepository(get_database_session_maker())


def get_repository_work_coordinator() -> MultiRepositoryWorkCoordinator:
    return MultiRepositoryWorkCoordinator(get_database_session_maker())


def get_admin_session_manager() -> AdminSessionManager:
    return AdminSessionManager(get_synced_store_client(), get_app_config().ADMIN)


@lru_cache(maxsize=1)
def get_token_manager() -> TokenManager:
    app_config: Final[AppConfig] = get_app_config()
    return TokenManager(
        get_token_store_client(),
        get_synced_store_client(),
        get_keydata_repository(),
        app_config.KEYS,
        app_config.JWKS.TOKEN_MANAGER,
    )


def get_filesystem_key_manager() -> FileSystemKeyManager:
    config: AppConfig = get_app_config()
    return FileSystemKeyManager(config.JWKS, config.KEYS)


def get_synced_store_key_state_manager() -> SyncedStoreKeyStateManager:
    return SyncedStoreKeyStateManager(get_synced_store_client())


@lru_cache(maxsize=1)
def get_distributed_lock_factory() -> RedisInstanceLockFactory:
    return RedisInstanceLockFactory(get_synced_store_client())


def get_key_lifecycle_manager() -> KeyLifecycleManager:
    return KeyLifecycleManager(
        get_keydata_repository(),
        get_token_manager(),
        get_filesystem_key_manager(),
        get_synced_store_key_state_manager(),
        get_distributed_lock_factory(),
    )
