from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from auxillary.data_structures.dto import AbstractResult

from auth_server.admin.roles import AdminRole
from auth_server.models.database import Admin


@dataclass(slots=True, init=False)
class AdminPublicResult(AbstractResult):
    """
    DTO for an Admin ORM object
    """

    resource_name: ClassVar[str] = Admin.__tablename__

    id: int
    username: str
    role: AdminRole

    time_deleted: datetime | None
    last_login: datetime
    locked: bool
    created_by: int
