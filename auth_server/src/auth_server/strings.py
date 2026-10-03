from enum import StrEnum
from typing import Final, LiteralString

GENERIC_SEPARATOR: Final[LiteralString] = ":"
GENERIC_SESSION_SEPARATOR: Final[LiteralString] = "."


class AdminStrings(StrEnum):
    SESSION_TOKEN_HEADER = "X-SESSION-TOKEN"  # nosec


class SyncedStoreCommandStrings(StrEnum):
    ABORT = "ABORT"
    AUTH_BOOTUP_MASTER = "AUTH_BOOTUP_MASTER"


class SyncedStoreKeyStrings(StrEnum):
    JWKS_KEY = "JWKS_KEY"
    KEY_ROTATION_LOCK = "KEY_ROTATION_LOCK"
    KEY_ROTATION_COOLDOWN = "KEY_ROTATION_COOLDOWN"
    INVALIDATE_KEY = "INVALIDATE_KEY"
    KEYSTORE_CLEAN = "KEYSTORE_CLEAN"


class SelectionLockOption(StrEnum):
    NOWAIT = "nowait"
    SKIP_LOCKED = "skip_locked"
    KEY_SHARE = "key_share"
    READ = "read"
