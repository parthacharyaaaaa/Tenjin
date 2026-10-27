from datetime import datetime
from typing import Annotated, ClassVar

from auxillary.data_structures.dto import AbstractResult
from pydantic import AliasChoices, Field

from auth_server.admin.roles import AdminRole
from auth_server.models.database import Admin


class AdminPublicResult(AbstractResult):
    """
    DTO for an Admin ORM object
    """

    resource_name: ClassVar[str] = Admin.__tablename__

    id: Annotated[int, Field(validation_alias=AliasChoices("id", "id_"))]
    username: str
    role: AdminRole

    time_deleted: datetime | None
    last_login: datetime
    locked: bool
    created_by: int
