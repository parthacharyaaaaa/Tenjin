from datetime import datetime
from typing import Any

from sqlalchemy.dialects.postgresql.json import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column
from sqlalchemy.schema import ColumnCollectionConstraint
from sqlalchemy.sql.expression import text
from sqlalchemy.sql.schema import CheckConstraint, Index
from sqlalchemy.types import BIGINT, SMALLINT, TEXT, TIMESTAMP, VARCHAR

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
    attempts: Mapped[int] = mapped_column(
        SMALLINT, nullable=False, server_default="0"
    )  # Postgres autocasts to SMALLINT
    added_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    lease_expiry_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    @classmethod
    def constraints(cls) -> t_table_args:
        return (
            CheckConstraint(
                cls.last_attempt_at.is_(None) | (cls.added_at <= cls.last_attempt_at),
                "ck_attempt_timestamp_chronology",
            ),
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
            CheckConstraint(cls.attempts >= 0, "ck_attempts_lower_bound"),
        )


class OutboxTableMixin(EventHolderMixin, RetriedProcessingMixin):
    message_id: Mapped[int] = mapped_column(
        BIGINT, primary_key=True, autoincrement=True
    )

    # NOTE: Indexing decisions are to be made by the user of this mixin class
    @classmethod
    def outbox_constraints(cls) -> t_table_args:
        return get_mixin_constraints(cls)

    @declared_attr.directive
    @classmethod
    def __table_args__(cls):
        return cls.outbox_constraints()


class InboxTableMixin(EventHolderMixin, RetriedProcessingMixin):
    message_id: Mapped[int] = mapped_column(
        BIGINT, primary_key=True, autoincrement=True
    )

    # NOTE: Indexing decisions are to be made by the user of this mixin class
    @classmethod
    def inbox_constraints(cls) -> t_table_args:
        return get_mixin_constraints(cls)

    @declared_attr.directive
    @classmethod
    def __table_args__(cls):
        return cls.inbox_constraints()


class TransactionalBoxDeadLetterMixin:
    id_: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)

    # NOTE: NON-Nullable attributes are also allowed to be NULL
    # here in case of malformmed messages
    message_id: Mapped[int | None] = mapped_column(BIGINT, nullable=True)
    payload: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    event_type: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    error_text: Mapped[str] = mapped_column(TEXT, nullable=False)

    last_attempt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(
        SMALLINT, nullable=False, server_default="0"
    )  # Postgres autocasts to SMALLINT

    @classmethod
    def constraints(cls) -> t_table_args:
        return (CheckConstraint(cls.attempt_count >= 0, "ck_attempts_lower_bound"),)
