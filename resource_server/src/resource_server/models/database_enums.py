from enum import StrEnum

from resource_auxillary.strings import SECOND_CLASS_EVENTS, EventName
from sqlalchemy.dialects.postgresql import ENUM

# from resource_auxillary.datastructures.database import SideEffectType


class AdminRoles(StrEnum):
    ADMIN = "ADMIN"
    SUPER = "SUPER"
    OWNER = "OWNER"


class ReportTags(StrEnum):
    SPAM = "SPAM"
    HARASSMENT = "HARASSMENT"
    HATE = "HATE"
    VIOLENCE = "VIOLENCE"
    OTHER = "OTHER"


ADMIN_ROLES = ENUM(
    AdminRoles,
    values_callable=lambda x: [e.value for e in x],
    name="ADMIN_ROLES",
    create_type=True,
)

REPORT_TAGS = ENUM(
    ReportTags,
    values_callable=lambda x: [e.value for e in x],
    name="REPORT_TAGS",
    create_type=True,
)

# SIDE_EFFECT_TYPES = ENUM(
#     *(i.value for i in SideEffectType), name=SideEffectType.__NAME__, create_type=True
# )

EVENT_NAME = ENUM(
    *(i.value for i in EventName if i not in SECOND_CLASS_EVENTS),
    name=EventName.__NAME__,
    create_type=True,
)
