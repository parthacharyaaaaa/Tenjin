from functools import lru_cache
import os

from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from resource_database_workers.config.config import AppConfig
from resource_database_workers.config.worker_config import WorkerSettings
from resource_database_workers.datastructures.queues import QueueRegistry


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    return AppConfig()  # type: ignore[reportCallIssue]


@lru_cache(maxsize=1)
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()  # type: ignore[reportCallIssue]


@lru_cache(maxsize=1)
def get_app_redis() -> Redis:
    app: AppConfig = get_config()
    return Redis(
        host=str(app.REDIS.APP.HOST), port=app.REDIS.APP.PORT, db=app.REDIS.APP.DB
    )


@lru_cache(maxsize=1)
def get_internal_redis() -> Redis:
    app: AppConfig = get_config()
    return Redis(
        host=str(app.REDIS.INTERNAL.HOST),
        port=app.REDIS.INTERNAL.PORT,
        db=app.REDIS.INTERNAL.DB,
    )


@lru_cache(maxsize=1)
def get_queue_registry() -> QueueRegistry:
    return QueueRegistry()


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
