from datetime import datetime
from typing import Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import CheckConstraint, ColumnCollectionConstraint, Index
from sqlalchemy.sql.expression import text
from sqlalchemy.types import BIGINT, SMALLINT, TIMESTAMP, VARCHAR

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
        SMALLINT, nullable=False, server_default="0"
    )  # Postgres autocasts to SMALLINT
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    lease_expiry_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    lease_token: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True)

    @classmethod
    def constraints(cls) -> t_table_args:
        return (
            CheckConstraint(
                (cls.last_attempt_at.is_(None) | cls.processed_at.is_(None))
                | (cls.last_attempt_at < cls.processed_at),
                "ck_processed_timestamp_chronology",
            ),
            CheckConstraint(
                (cls.lease_expiry_at.is_(None) | cls.last_attempt_at.is_(None))
                | (cls.lease_expiry_at > cls.last_attempt_at),
                "ck_lease_chronology",
            ),
            CheckConstraint(
                cls.lease_expiry_at > cls.last_attempt_at,
                "ck_lease_chronology",
            ),
            CheckConstraint(cls.attempts >= 0, "ck_attempts_lower_bound"),
        )


class EventSchedulerMixin:
    # Recommended to be narrowed with an enum
    subscriber_type: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    last_scheduled_id: Mapped[int] = mapped_column(
        BIGINT, nullable=False, server_default=text("0")
    )

    @classmethod
    def constraints(cls) -> t_table_args:
        return (CheckConstraint(cls.last_scheduled_id >= 0, "ck_last_scheduled_id"),)
