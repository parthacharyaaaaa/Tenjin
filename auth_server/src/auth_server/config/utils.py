"""Config-related constants"""

from typing import Final
import re

__all__ = ("APP_ROOT_PATTERN", "JWKS_NAME_PATTERN", "IDENTITY_PATTERN", "EMAIL_PATTERN")

APP_ROOT_PATTERN: Final[re.Pattern] = re.compile(r"^/api/v\d+$")
JWKS_NAME_PATTERN: Final[re.Pattern] = re.compile(r"^.+\.json$")

IDENTITY_PATTERN: Final[re.Pattern] = re.compile(r"^\w+$")
EMAIL_PATTERN: Final[re.Pattern] = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)
