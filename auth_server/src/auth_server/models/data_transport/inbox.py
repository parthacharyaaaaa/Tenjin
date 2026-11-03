from datetime import datetime
from typing import Annotated, ClassVar, Self

from auxillary.data_structures.dto import AbstractResult
from pydantic import Field
from pydantic.functional_validators import model_validator

from auth_server.models.database import InboxTable


class InboxEventResult(AbstractResult):
    resource_name: ClassVar[str] = InboxTable.__tablename__

    message_id: int
    attempts: Annotated[int, Field(ge=0)]
    added_at: datetime
    last_attempt_at: datetime | None
    processed_at: datetime | None
    lease_expiry_at: datetime | None

    @model_validator(mode="after")
    def validate_chronology(self) -> Self:
        # None checks
        if not (bool(self.last_attempt_at) ^ bool(self.lease_expiry_at)):
            raise ValueError(
                "Invalid nullability of last attempt and lease expiry column"
            )
        if self.last_attempt_at is None:
            if self.processed_at is not None:
                raise ValueError("Processed record with no last attempt timestampt")
            return self
        if self.lease_expiry_at is None:
            return self
        # Chronology checks
        if self.added_at > self.last_attempt_at:
            raise ValueError(
                f"last attempt {self.last_attempt_at.isoformat()} done prior to "
                f"recording time {self.added_at.isoformat()}"
            )
        if self.last_attempt_at >= self.lease_expiry_at:
            raise ValueError(
                f"last attempt {self.last_attempt_at.isoformat()} greater "
                f"than attempt's lease {self.lease_expiry_at.isoformat()}"
            )
        if self.processed_at and self.processed_at <= self.last_attempt_at:
            raise ValueError(
                f"last attempt {self.last_attempt_at.isoformat()} lesser than/"
                f"equal to processed timestamp {self.processed_at.isoformat()}"
            )
        return self
