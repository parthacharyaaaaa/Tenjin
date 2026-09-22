from enum import StrEnum


class Permission(StrEnum):
    READ_KEY = "read_key"
    ROTATE_KEY = "rotate_key"
    INVALIDATE_KEY = "invalidate_key"
    CLEAN_KEYSTORE = "clean_keystore"

    DELETE_ADMIN = "delete_admin"
    LOCK_ADMIN = "lock_admin"
    UNLOCK_ADMIN = "unlock_admin"
    CREATE_ADMIN = "create_admin"
