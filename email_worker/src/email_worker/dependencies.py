from functools import lru_cache
import os
from typing import Final

from aiosmtplib import SMTP
from ssl import create_default_context, Purpose

from email_worker.config.email_config import EmailConfig


@lru_cache(maxsize=1)
def get_email_config() -> EmailConfig:
    return EmailConfig()  # type: ignore[reportCallIssue]


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
