from enum import StrEnum
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

# NOTE: Unused, columns in RetriedProcessingMixin are sufficient
# in deriving processing state

# class InboxProcessingState(StrEnum):
#     PENDING = "PENDING"
#     PROCESSING = "PROCESSING"
#     PROCESSED = "PROCESSED"
# INBOX_PROCESSING_STATES = ENUM(
#     InboxProcessingState,
#     name="inbox_processing_states",
#     values_callable=lambda x: [e.value for e in x],
#     create_type=True,
# )


class InboxErrorTypes(StrEnum):
    GENERAL_PROCESSING_ERROR = "GENERAL_PROCESSING_ERROR"
    GENERAL_NETWORK_FAILURE = "GENERAL_NETWORK_FAILURE"
    REDIS_TIMEOUT = "REDIS_TIMEOUT"
    REDIS_NETWORK_FAILURE = "REDIS_NETWORK_FAILURE"
    REDIS_PROCESSING_ERROR = "REDIS_PROCESSING_ERROR"
    MALFORMED_MESSAGE = "MALFORMED_MESSAGE"


INBOX_ERROR_TYPES = ENUM(
    InboxErrorTypes,
    name="inbox_error_types",
    values_callable=lambda x: [e.value for e in x],
    create_type=True,
)
