from datetime import datetime
from typing import Any

from sqlalchemy.dialects.postgresql.json import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column
from sqlalchemy.sql.expression import text
from sqlalchemy.sql.schema import CheckConstraint
from sqlalchemy.types import BIGINT, SMALLINT, TEXT, TIMESTAMP, VARCHAR

from auxillary.mixins.transactional_boxes.auxillary import (
    EventHolderMixin,
    get_mixin_constraints,
    t_table_args,
)


class TransactionalBoxMixin(EventHolderMixin):
    message_id: Mapped[int] = mapped_column(
        BIGINT, primary_key=True, autoincrement=True
    )
    added_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    # NOTE: Indexing decisions are to be made by the user of this mixin class
    @classmethod
    def table_constraints(cls) -> t_table_args:
        return get_mixin_constraints(cls)

    @declared_attr.directive
    @classmethod
    def __table_args__(cls):
        return cls.table_constraints()


class TransactionalBoxDeadLetterMixin:
    id_: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)

    # NOTE: NON-Nullable attributes are also allowed to be NULL
    # here in case of malformmed messages
    # Also why we couldn't inherit from EventHolderMixin
    message_id: Mapped[int | None] = mapped_column(BIGINT, nullable=True)
    payload: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    event_type: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    error_text: Mapped[str] = mapped_column(TEXT, nullable=False)

    last_attempt: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(
        SMALLINT, nullable=False
    )  # Postgres autocasts to SMALLINT

    @classmethod
    def table_constraints(cls) -> t_table_args:
        return (CheckConstraint(cls.attempt_count >= 0, "ck_attempts_lower_bound"),)
