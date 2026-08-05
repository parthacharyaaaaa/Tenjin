from typing import Final

from aiosmtplib.errors import (
    SMTPException,
    SMTPTimeoutError,
    SMTPReadTimeoutError,
    SMTPServerDisconnected,
)

UNSAFE_SMTP_NETWORK_ERRORS: Final[tuple[type[SMTPException], ...]] = (
    SMTPReadTimeoutError,
    SMTPServerDisconnected,
)

POTENTIALLY_TRANSIENT_SMTP_ERRORS: Final[tuple[type[SMTPException], ...]] = (
    SMTPReadTimeoutError,
    SMTPTimeoutError,
    SMTPServerDisconnected,
)
