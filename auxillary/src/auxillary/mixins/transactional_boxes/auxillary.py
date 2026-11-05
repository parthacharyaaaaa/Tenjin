from datetime import datetime
from typing import Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import CheckConstraint, ColumnCollectionConstraint, Index
from sqlalchemy.types import SMALLINT, TIMESTAMP, VARCHAR

type t_table_args = tuple[ColumnCollectionConstraint | Index, ...]


def get_mixin_constraints(model_class: type[Any]) -> t_table_args:
    constraints: list[ColumnCollectionConstraint | Index] = []
    for base in model_class.__mro__:
        contributor = base.__dict__.get("constraints")
        if isinstance(contributor, classmethod):
            constraints.extend(contributor.__func__(model_class))
    return tuple(constraints)


class EventHolderMixin:
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)
    # NOTE: It is recommended to change a subclass' event_type attribute to a StrEnum
    event_type: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)


class RetriedProcessingMixin:
    # Default to 1 and not 0 since subscribers record their entries
    # on their first attempts
    attempts: Mapped[int] = mapped_column(
        SMALLINT, nullable=False, server_default="1"
    )  # Postgres autocasts to SMALLINT
    last_attempt_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    lease_expiry_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    lease_token: Mapped[str] = mapped_column(VARCHAR(32))

    @classmethod
    def constraints(cls) -> t_table_args:
        return (
            CheckConstraint(
                cls.processed_at.is_(None) | (cls.last_attempt_at < cls.processed_at),
                "ck_processed_timestamp_chronology",
            ),
            CheckConstraint(
                cls.lease_expiry_at > cls.last_attempt_at,
                "ck_lease_chronology",
            ),
            CheckConstraint(cls.attempts >= 1, "ck_attempts_lower_bound"),
        )
