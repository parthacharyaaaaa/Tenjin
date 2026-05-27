from enum import Enum


class SyncedStoreStrings(str, Enum):
    ABORT = "ABORT"
    AUTH_BOOTUP_MASTER = "AUTH_BOOTUP_MASTER"
    VALID_KEYS = "VALID_KEYS"
