from typing import Annotated, Self
from uuid import uuid4

from auth_server.security.admin_roles import AdminRole
from pydantic import BaseModel, Field, ConfigDict, model_validator


class AdminSession(BaseModel):
    model_config = ConfigDict(frozen=True)

    id_: Annotated[
        int, Field(ge=1, serialization_alias="id", default_factory=lambda: uuid4().int)
    ]
    admin_id: Annotated[int, Field(ge=1)]
    expiry_at: Annotated[float, Field(ge=1)]
    epoch: Annotated[float, Field(ge=1)]
    role: Annotated[AdminRole, Field(default=AdminRole.STAFF)]
    iteration: Annotated[int, Field(default=1)]

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        if self.epoch >= self.expiry_at:
            raise ValueError(
                " ".join(
                    (
                        f"Session epoch time {self.epoch}",
                        "cannot be greater than session expiry",
                        str(self.expiry_at),
                    )
                )
            )
        return self

    @property
    def session_key(self) -> str:
        return f"admin:{self.admin_id}"

    def validate_with_server_session(
        self, id_: int, expiry: float, iteration: int, role: AdminRole
    ) -> bool:
        return (
            self.admin_id == id_
            and self.expiry_at == expiry
            and self.iteration == iteration
            and self.role == AdminRole(role)
        )
