from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from redis.typing import DecodedT, KeyT
from sqlalchemy.util.typing import TypeGuard

from auth_server.admin.roles import AdminRole


class AdminSession(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    admin_id: Annotated[int, Field(ge=1)]
    expiry_timestamp: Annotated[int, Field(ge=1)]
    revival_digest: str
    epoch_timestamp: Annotated[int, Field(ge=1)]
    role: Annotated[AdminRole, Field(default=AdminRole.STAFF)]
    iteration: Annotated[int, Field(default=1)]

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        if self.epoch_timestamp >= self.expiry_timestamp:
            raise ValueError(
                " ".join(
                    (
                        f"Session epoch_timestamp time {self.epoch_timestamp}",
                        "must be lesser than session expiry",
                        str(self.expiry_timestamp),
                    )
                )
            )
        return self

    @staticmethod
    def _is_redis_dict(value: dict[str, Any]) -> TypeGuard[dict[KeyT, DecodedT]]:
        return all(
            isinstance(k, KeyT) and isinstance(v, DecodedT) for k, v in value.items()
        )

    def model_dump_redis(self) -> dict[KeyT, DecodedT]:
        model_dump: dict[str, Any] = self.model_dump()
        model_dump["role"] = model_dump["role"].value

        assert self._is_redis_dict(model_dump)  # nosec

        return model_dump

    @classmethod
    def construct_session_successor(
        cls,
        preceding_session: "AdminSession",
        new_session_id: str,
        epoch_timestamp: float,
        expiry_timestamp: float,
        revival_digest: str,
    ) -> Self:
        return cls(
            session_id=new_session_id,
            admin_id=preceding_session.admin_id,
            epoch_timestamp=epoch_timestamp,
            expiry_timestamp=expiry_timestamp,
            revival_digest=revival_digest,
            role=preceding_session.role,
            iteration=preceding_session.iteration + 1,
        )
