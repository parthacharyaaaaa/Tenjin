from functools import lru_cache
import os
from typing import Final

from aiosmtplib import SMTP
from ssl import create_default_context, Purpose

from redis.asyncio import Redis

from email_worker.config.email_config import EmailConfig
from email_worker.config.redis_config import RedisConfig


@lru_cache(maxsize=1)
def get_email_config() -> EmailConfig:
    return EmailConfig()  # type: ignore[reportCallIssue]


@lru_cache(maxsize=1)
def get_redis_config() -> RedisConfig:
    return RedisConfig()  # type: ignore[reportCallIssue]


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
