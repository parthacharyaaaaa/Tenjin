from typing import Final


class UserConstants:
    USERNAME_MAX_LENGTH: Final[int] = 64
    USERNAME_MIN_LENGTH: Final[int] = 4

    # 254 byte length email addresses
    # (RFC 3696: https://www.rfc-editor.org/rfc/rfc3696#page-5,
    # errata: https://www.rfc-editor.org/errata_search.php?rfc=3696)
    EMAIL_MAX_LENGTH: Final[int] = 254
    EMAIL_MIN_LENGTH: Final[int] = 5  # x@x.x

    # Bcrypt produces a 60-byte salted hash (when using $2a$ or $2y$ as identifier)
    PASSWORD_HASH_LENGTH: Final[int] = 60


class PostConstants:
    TITLE_MIN_LENGTH: Final[int] = 4
    TITLE_MAX_LENGTH: Final[int] = 16

    BODY_MAX_LENGTH: Final[int] = 32800

    REPORT_DESCRIPTION_MAX_LENGTH: Final[int] = 256
    REPORT_DESCRIPTION_MIN_LENGTH: Final[int] = 8


class CommentConstants:
    COMMENT_MAX_LENGTH: Final[int] = 8192
    COMMENT_MIN_LENGTH: Final[int] = 1


class ForumConstants:
    RULE_TITLE_MIN_LENGTH: Final[int] = 8
    RULE_TITLE_MAX_LENGTH: Final[int] = 32

    RULE_DESCRIPTION_MIN_LENGTH: Final[int] = 8
    RULE_DESCRIPTION_MAX_LENGTH: Final[int] = 128

    TITLE_MIN_LENGTH: Final[int] = 4
    TITLE_MAX_LENGTH: Final[int] = 64
    DESCRIPTION_MAX_LENGTH: Final[int] = 256
    DESCRIPTION_MIN_LENGTH: Final[int] = 8


class AnimeConstants:
    TITLE_MAX_LENGTH: Final[int] = 64
    DESCRIPTION_MAX_LENGTH: Final[int] = 256
    DESCRIPTION_MIN_LENGTH: Final[int] = 8

    HIGHEST_MAL_RATING: Final[int] = 10


class GenreConstants:
    TITLE_MAX_LENGTH: Final[int] = 16
    TITLE_MIN_LENGTH: Final[int] = 4


class PasswordRecoveryConstants:
    URL_HASH_LENGTH: Final[int] = 512


class UserTicketConstants:
    DESCRIPTION_MAX_LENGTH: Final[int] = 512
    DESCRIPTION_MIN_LENGTH: Final[int] = 8


class StreamLinkConstants:
    URL_MAX_LENGTH: Final[int] = 512
