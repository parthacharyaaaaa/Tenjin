import os
from functools import lru_cache
from ssl import Purpose, create_default_context
from typing import Final

from aiosmtplib import SMTP
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis
from resource_auxillary.event_processing.event_stream_manager import (
    EventStreamManager,
    RedisStreamManager,
)
from resource_auxillary.strings import StreamName

from email_worker.config.config import AppConfig
from email_worker.config.sub_config import DatabaseConfig, EmailConfig, RedisConfig
from email_worker.datastructures.queue_registry import QueueRegistry


@lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    return AppConfig()  # type: ignore[reportCallIssue]


@lru_cache(maxsize=1)
def get_email_config() -> EmailConfig:
    return get_app_config().EMAIL


@lru_cache(maxsize=1)
def get_redis_config() -> RedisConfig:
    return get_app_config().REDIS


@lru_cache(maxsize=1)
def get_database_config() -> DatabaseConfig:
    return get_app_config().DATABASE


@lru_cache(maxsize=1)
def get_redis_client() -> Redis:
    redis_config: RedisConfig = get_redis_config()
    return Redis(
        host=redis_config.HOSTNAME,
        port=redis_config.PORT,
        db=redis_config.DB,
        decode_responses=redis_config.DECODE_RESPONSES,
    )


async def get_fresh_smtp_client() -> SMTP:
    email_config: Final[EmailConfig] = get_email_config()
    smtp_client: SMTP = SMTP(
        hostname=email_config.hostname,
        port=email_config.port,
        username=os.environ["SMTP_USERNAME"],
        password=os.environ["SMTP_PASSWORD"],
        use_tls=email_config.use_tls,
        tls_context=create_default_context(purpose=Purpose.CLIENT_AUTH),
    )
    await smtp_client.connect()
    return smtp_client


def get_event_stream_manager() -> EventStreamManager:
    return RedisStreamManager(get_redis_client())


@lru_cache(maxsize=1)
def get_consumer_id() -> str:
    return str(os.getpid())


@lru_cache(maxsize=1)
def get_connection_pool() -> AsyncConnectionPool:
    db_config: DatabaseConfig = get_database_config()
    uri: str = db_config.derive_sqlalchemy_uri(
        os.environ["POSTGRES_USERNAME"],
        os.environ["POSTGRES_PASSWORD"],
    )

    return AsyncConnectionPool(
        conninfo=uri,
        **config.DATABASE.emit_connection_pool_constructor_kwargs(),  # noqa
    )


@lru_cache(maxsize=1)
def get_queue_registry() -> QueueRegistry:
    return QueueRegistry()


@lru_cache(maxsize=1)
def get_dead_letter_queue_name() -> StreamName:
    return StreamName.DEAD_LETTER_QUEUE
