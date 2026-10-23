from typing import Final

# User Authentication
MIN_IDENTITY_LENGTH: Final[int] = 4
MAX_IDENTITY_LENGTH: Final[int] = 32

MIN_PASSWORD_LENGTH: Final[int] = 8
MAX_PASSWORD_LENGTH: Final[int] = 64

# x@x.xx -> 6
MIN_EMAIL_LENGTH: Final[int] = 6
MAX_EMAIL_LENGTH: Final[int] = 254
# 254 byte length email addresses
# (RFC 3696: https://www.rfc-editor.org/rfc/rfc3696#page-5,
# errata: https://www.rfc-editor.org/errata_search.php?rfc=3696)

# Admin Sessions
REVIVAL_DIGEST_LENGTH: Final[int] = 256

# Key-related
KEY_IDENTIFIER_LENGTH: Final[int] = 32
EC_CURVE_LENGTH: Final[int] = 32
ALGORITHM_NAME_LEMGTH: Final[int] = 32

# Suspicious-Activity related
DESCRIPTION_MAX_LENGTH: Final[int] = 1024
DESCRIPTION_MIN_LENGTH: Final[int] = 8
