import os
from functools import lru_cache
from typing import Final

from redis import Redis

from auth_server.config import AppConfig


@lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    return AppConfig()  # type: ignore[reportCallIssue]


@lru_cache(maxsize=1)
def get_synced_store_client() -> Redis:
    config: Final[AppConfig] = get_app_config()

    return Redis(
        username=os.environ["AUTH_WORKER_REDIS_USERNAME"],
        password=os.environ["AUTH_WORKER_REDIS_PASSWORD"],
        **config.REDIS.SYNCED_STORE.to_constructor_kwargs(),
    )


@lru_cache(maxsize=1)
def get_token_store_client() -> Redis:
    config: Final[AppConfig] = get_app_config()

    return Redis(
        username=os.environ["AUTH_WORKER_REDIS_USERNAME"],
        password=os.environ["AUTH_WORKER_REDIS_PASSWORD"],
        **config.REDIS.TOKEN_STORE.to_constructor_kwargs(),
    )
