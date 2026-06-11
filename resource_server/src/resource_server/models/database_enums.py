from enum import StrEnum


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
