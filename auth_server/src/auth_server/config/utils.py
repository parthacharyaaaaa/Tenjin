"""Config-related constants"""

from typing import Final
import re

__all__ = ("APP_ROOT_PATTERN", "JWKS_NAME_PATTERN")

APP_ROOT_PATTERN: Final[re.Pattern] = re.compile(r"^/api/v\d+$")
JWKS_NAME_PATTERN: Final[re.Pattern] = re.compile(r"^.+\.json$")
