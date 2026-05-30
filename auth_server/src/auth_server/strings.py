from enum import StrEnum


class AdminStrings(StrEnum):
    SESSION_TOKEN_HEADER = "X-SESSION-TOKEN"


class SyncedStoreStrings(StrEnum):
    ABORT = "ABORT"
    AUTH_BOOTUP_MASTER = "AUTH_BOOTUP_MASTER"
    VALID_KEYS = "VALID_KEYS"
