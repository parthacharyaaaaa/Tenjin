from functools import lru_cache

from email_worker.config.email_config import EmailWorkerConfig


@lru_cache(maxsize=1)
def get_email_config() -> EmailWorkerConfig:
    return EmailWorkerConfig()  # type: ignore[reportCallIssue]
