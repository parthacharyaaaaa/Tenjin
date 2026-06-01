from functools import lru_cache

from resource_server.config.app_config import AppConfig


@lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    return AppConfig()  # type: ignore[reportCallIssue]
