from enum import StrEnum
from typing import Final
from types import MappingProxyType

from resource_server.models.database_enums import AdminRoles


class AdminPermissions(StrEnum):
    ADD_RULE = "ADD_RULE"
    ADD_ADMIN = "ADD_ADMIN"
    ADD_SUPER = "ADD_SUPER"

    EDIT_FORUM = "EDIT_FORUM"

    DEMOTE_TO_ADMIN = "DEMOTE_TO_ADMIN"
    PROMOTE_TO_SUPER = "PROMOTE_TO_SUPER"

    REMOVE_ADMIN = "REMOVE_ADMIN"
    REMOVE_SUPER = "REMOVE_SUPER"

    DELETE_COMMENT = "DELETE_COMMENT"
    DELETE_POST = "DELETE_POST"
    DELETE_RULE = "DELETE_RULE"
    DELETE_FORUM = "DELETE_FORUM"


_OWNER_EXCLUSIVE_PERMISSIONS: tuple[AdminPermissions, ...] = (
    AdminPermissions.ADD_SUPER,
    AdminPermissions.REMOVE_SUPER,
    AdminPermissions.DELETE_FORUM,
)

type t_permissions_mapping = MappingProxyType[AdminRoles, tuple[AdminPermissions, ...]]

ADMIN_PERMISSIONS_MAPPING: Final[t_permissions_mapping] = MappingProxyType(
    {
        AdminRoles.OWNER: tuple(i for i in AdminPermissions),
        AdminRoles.SUPER: tuple(
            set(i for i in AdminPermissions) - set(_OWNER_EXCLUSIVE_PERMISSIONS)
        ),
        AdminRoles.ADMIN: (
            AdminPermissions.DELETE_POST,
            AdminPermissions.DELETE_COMMENT,
        ),
    }
)


def check_permission(role: AdminRoles, permission: AdminPermissions) -> bool:
    return permission in ADMIN_PERMISSIONS_MAPPING[role]
