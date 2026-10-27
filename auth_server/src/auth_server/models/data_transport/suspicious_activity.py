from datetime import datetime
from typing import Annotated, ClassVar

from auxillary.data_structures.dto import AbstractResult
from pydantic import AliasChoices, Field

from auth_server.models.database import SuspiciousActivity


class SuspiciousActivityResult(AbstractResult):
    """
    DTO for SuspiciousActivity ORM object
    """

    resource_name: ClassVar[str] = SuspiciousActivity.__tablename__

    id: Annotated[int, Field(validation_alias=AliasChoices("id", "id_"))]
    suspect: int
    time_logged: datetime
    description: str
