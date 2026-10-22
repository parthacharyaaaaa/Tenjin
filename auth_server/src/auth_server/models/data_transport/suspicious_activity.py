from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from auxillary.data_structures.dto import AbstractResult

from auth_server.models.database import SuspiciousActivity


@dataclass(slots=True, init=False)
class SuspiciousActivityResult(AbstractResult):
    """
    DTO for SuspiciousActivity ORM object
    """

    resource_name: ClassVar[str] = SuspiciousActivity.__tablename__

    id_: int
    suspect: int
    time_logged: datetime
    description: str
