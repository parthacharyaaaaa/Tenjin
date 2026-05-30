from enum import StrEnum
from types import MappingProxyType
from typing import Final

from auth_server.security.permissions import Permission


class AdminRole(StrEnum):
    SUPER = "super"
    STAFF = "staff"


ROLE_PERMISSIONS: Final[MappingProxyType[AdminRole, tuple[Permission, ...]]] = (
    MappingProxyType(
        {
            AdminRole.SUPER: tuple(p for p in Permission),
            AdminRole.STAFF: (Permission.READ_KEY, Permission.ROTATE_KEY),
        }
    )
)
