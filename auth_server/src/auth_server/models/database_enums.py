from typing import Final

from sqlalchemy.dialects.postgresql import ENUM

from auth_server.admin.roles import AdminRole, Permission

ADMIN_ROLES: Final[ENUM] = ENUM(
    AdminRole,
    name="admin_roles",
    values_callable=lambda x: [e.value for e in x],
    create_type=True,
)
ADMIN_PERMISSIONS = ENUM(
    Permission,
    name="admin_permissions",
    values_callable=lambda x: [e.value for e in x],
    create_type=True,
)
