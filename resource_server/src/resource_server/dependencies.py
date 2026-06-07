import os
from functools import lru_cache
from typing import AsyncGenerator, Final

from redis.asyncio import Redis

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
    AsyncEngine,
)

from resource_server.cache_manager import CacheManager
from resource_server.config.app_config import AppConfig
from resource_server.key_manager import KeyManager
from resource_server.models.database import Genre
from resource_server.repositories.anime import AnimeRepository
from resource_server.event_streamer import EventStreamer


@lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    return AppConfig()  # type: ignore[reportCallIssue]


@lru_cache(maxsize=1)
def get_app_redis_client() -> Redis:
    config: Final[AppConfig] = get_app_config()

    return Redis(
        host=str(config.REDIS.APP.HOST),
        port=config.REDIS.APP.PORT,
        db=config.REDIS.APP.DB,
        username=os.environ["RESOURCE_WORKER_REDIS_USERNAME"],
        password=os.environ["RESOURCE_WORKER_REDIS_PASSWORD"],
    )


@lru_cache(maxsize=1)
def get_auth_redis_client() -> Redis:
    config: Final[AppConfig] = get_app_config()

    return Redis(
        host=str(config.REDIS.AUTH.HOST),
        port=config.REDIS.AUTH.PORT,
        db=config.REDIS.AUTH.DB,
        username=os.environ["RESOURCE_AUTH_WORKER_REDIS_USERNAME"],
        password=os.environ["RESOURCE_AUTH_WORKER_REDIS_PASSWORD"],
    )


@lru_cache(maxsize=1)
def get_key_manager() -> KeyManager:
    return KeyManager(get_app_config(), get_app_redis_client(), get_auth_redis_client())


@lru_cache(maxsize=1)
def get_cache_manager() -> CacheManager:
    return CacheManager(get_app_redis_client(), get_app_config().CACHE)


@lru_cache(maxsize=1)
def get_event_streamer() -> EventStreamer:
    return EventStreamer(get_app_redis_client(), get_app_config().CACHE)


@lru_cache(maxsize=1)
def get_database_session_maker() -> async_sessionmaker[AsyncSession]:
    config: Final[AppConfig] = get_app_config()

    URI: Final[str] = config.DATABASE.SQLALCHEMY.derive_sqlalchemy_uri(
        username=os.environ["RESOURCE_SERVER_POSTGRES_USERNAME"],
        password=os.environ["RESOURCE_SERVER_POSTGRES_PASSWORD"],
        host=str(config.DATABASE.POSTGRES_HOST),
        port=config.DATABASE.POSTGRES_PORT,
        database=config.DATABASE.POSTGRES_DATABASE,
    )

    engine: Final[AsyncEngine] = create_async_engine(URI)

    session_maker: Final[async_sessionmaker[AsyncSession]] = async_sessionmaker(
        bind=engine, autocommit=False, autoflush=False
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
async def get_genres() -> dict[str, int]:
    async with get_database_session_maker()() as session:
        genres: list[Genre] = list(
            (await session.execute(select(Genre))).scalars().all()
        )

        return {g.name_: g.id_ for g in genres}


@lru_cache(maxsize=1)
def get_anime_repository() -> AnimeRepository:
    return AnimeRepository(get_database_session_maker(), get_cache_manager())
