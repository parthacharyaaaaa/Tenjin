import os
from functools import lru_cache
from typing import AsyncGenerator, Final

from auth_server.repositories.keydata import KeydataRepository
from redis.asyncio import Redis

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
    AsyncEngine,
)

from auth_server.config import AppConfig
from auth_server.security.token_manager import TokenManager


@lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    return AppConfig()  # type: ignore[reportCallIssue]


@lru_cache(maxsize=1)
def get_synced_store_client() -> Redis:
    config: Final[AppConfig] = get_app_config()

    return Redis(
        # username=os.environ["AUTH_WORKER_REDIS_USERNAME"],
        # password=os.environ["AUTH_WORKER_REDIS_PASSWORD"],
        **config.REDIS.SYNCED_STORE.to_constructor_kwargs(),
    )


@lru_cache(maxsize=1)
def get_token_store_client() -> Redis:
    config: Final[AppConfig] = get_app_config()

    return Redis(
        # username=os.environ["AUTH_WORKER_REDIS_USERNAME"],
        # password=os.environ["AUTH_WORKER_REDIS_PASSWORD"],
        **config.REDIS.TOKEN_STORE.to_constructor_kwargs(),
    )


@lru_cache(maxsize=1)
def get_database_session_maker() -> async_sessionmaker[AsyncSession]:
    config: Final[AppConfig] = get_app_config()

    URI: Final[str] = config.DATABASE.SQLALCHEMY.derive_sqlalchemy_uri(
        username=os.environ["AUTH_WORKER_POSTGRES_USERNAME"],
        password=os.environ["AUTH_WORKER_POSTGRES_PASSWORD"],
        host=str(config.DATABASE.POSTGRES_HOST),
        port=config.DATABASE.POSTGRES_PORT,
        database=config.DATABASE.POSTGRES_DATABASE,
    )

    engine: Final[AsyncEngine] = create_async_engine(URI)

    session_maker: Final[async_sessionmaker[AsyncSession]] = async_sessionmaker(
        bind=engine, autocommit=False, autoflush=False, expire_on_commit=False
    )

    return session_maker


async def get_database_session() -> AsyncGenerator[AsyncSession, None]:
    session_maker: async_sessionmaker = get_database_session_maker()
    session: Final[AsyncSession] = session_maker()
    try:
        yield session
    finally:
        await session.close()


@lru_cache(maxsize=1)
def get_keydata_repository() -> KeydataRepository:
    return KeydataRepository(get_database_session_maker())


@lru_cache(maxsize=1)
def get_token_manager() -> TokenManager:
    return TokenManager(
        interface=get_token_store_client(),
        synced_store=get_synced_store_client(),
        keydata_repository=get_keydata_repository(),
    )
