from enum import StrEnum
from typing import Final, LiteralString

GENERIC_SEPARATOR: Final[LiteralString] = ":"


class AdminStrings(StrEnum):
    SESSION_TOKEN_HEADER = "X-SESSION-TOKEN"  # nosec
    ADMIN_KEY_CACHE = "ADMIN_KEY_CACHE"
    NO_REFRESH_SENTINEL = "__NONE__"


class SyncedStoreStrings(StrEnum):
    ABORT = "ABORT"
    AUTH_BOOTUP_MASTER = "AUTH_BOOTUP_MASTER"
    VALID_KEYS = "VALID_KEYS"
    KEY_ROTATION_LOCK = "KEY_ROTATION_LOCK"
    KEY_ROTATION_COOLDOWN = "KEY_ROTATION_COOLDOWN"
    ADMIN_SESSION_PREFIX = "admin"


class SelectionLockOption(StrEnum):
    NOWAIT = "nowait"
    SKIP_LOCKED = "skip_locked"
    KEY_SHARE = "key_share"
    READ = "read"


def generate_admin_session_name(admin_id: int | str) -> str:
    return GENERIC_SEPARATOR.join(
        (
            SyncedStoreStrings.ADMIN_SESSION_PREFIX,
            admin_id if isinstance(admin_id, str) else str(admin_id),
        )
    )
