import os
from functools import lru_cache
from typing import Final

from resource_server.config.app_config import AppConfig

from redis.asyncio import Redis
from resource_server.key_manager import KeyManager


@lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    return AppConfig()  # type: ignore[reportCallIssue]


@lru_cache(maxsize=1)
def get_redis_client() -> Redis:
    config: Final[AppConfig] = get_app_config()

    return Redis(
        host=str(config.REDIS.HOST),
        port=config.REDIS.PORT,
        db=config.REDIS.DB,
        username=os.environ["RESOURCE_SERVER_REDIS_USERNAME"],
        password=os.environ["RESOURCE_SERVER_REDIS_PASSWORD"],
    )


@lru_cache(maxsize=1)
def get_key_manager() -> KeyManager:
    return KeyManager(get_app_config(), get_redis_client())
