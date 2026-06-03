import re
from typing import Final

DOMAIN_REGEX: Final[re.Pattern] = re.compile(
    r"^((?!-)[A-Za-z0-9-]{1, 63}(?<!-)\\.)+[A-Za-z]{2, 6}$"
)

EMAIL_PATTERN: Final[re.Pattern] = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)
