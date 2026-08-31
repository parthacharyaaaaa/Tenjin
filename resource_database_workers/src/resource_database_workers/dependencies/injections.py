from resource_auxillary.event_processing.event_stream_manager import (
    EventStreamManager,
    RedisStreamManager,
)
from resource_database_workers.datastructures.queues import EventQueueRegistryContainer
from resource_auxillary.strings import StreamName
from functools import lru_cache
import os

from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from resource_database_workers.config.config import AppConfig


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    return AppConfig()  # type: ignore[reportCallIssue]


@lru_cache(maxsize=1)
def get_app_redis() -> Redis:
    app: AppConfig = get_config()
    return Redis(host=app.REDIS.APP.HOST, port=app.REDIS.APP.PORT, db=app.REDIS.APP.DB)


@lru_cache(maxsize=1)
def get_internal_redis() -> Redis:
    app: AppConfig = get_config()
    return Redis(
        host=app.REDIS.INTERNAL.HOST,
        port=app.REDIS.INTERNAL.PORT,
        db=app.REDIS.INTERNAL.DB,
    )


@lru_cache(maxsize=1)
def get_queue_registry() -> EventQueueRegistryContainer:
    return EventQueueRegistryContainer()


@lru_cache(maxsize=1)
def get_connection_pool() -> AsyncConnectionPool:
    config = get_config()
    uri: str = config.DATABASE.derive_sqlalchemy_uri(
        os.environ["POSTGRES_USERNAME"],
        os.environ["POSTGRES_PASSWORD"],
    )

    return AsyncConnectionPool(
        conninfo=uri,
        **config.DATABASE.emit_connection_pool_constructor_kwargs(),  # type: ignore
    )


@lru_cache(maxsize=1)
def get_consumer_id() -> str:
    return str(os.getpid())


@lru_cache(maxsize=1)
def get_dead_letter_queue_name() -> StreamName:
    # I wanted to have this as a DI
    # in case we ever decide to have a multiple/dynamic DLQ naming scheme
    return StreamName.DEAD_LETTER_QUEUE


@lru_cache(maxsize=1)
def get_stream_manager() -> EventStreamManager:
    """Current Implementation: Redis Streams"""
    redis_client = Redis = get_internal_redis()
    return RedisStreamManager(redis_client)
