import datetime

from auxillary.mixins.transactional_boxes.auxillary import (
    RetriedProcessingMixin,
    t_table_args,
)
from auxillary.mixins.transactional_boxes.transactional_boxes import (
    TransactionalBoxDeadLetterMixin,
    TransactionalBoxMixin,
)
from sqlalchemy import (
    BOOLEAN,
    INTEGER,
    TIMESTAMP,
    VARCHAR,
    ForeignKey,
    PrimaryKeyConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column
from sqlalchemy.schema import CheckConstraint
from sqlalchemy.sql.functions import func
from sqlalchemy.sql.schema import Index
from sqlalchemy.types import BIGINT

from auth_server.admin.roles import AdminRole
from auth_server.config.constants import (
    ALGORITHM_NAME_LEMGTH,
    DESCRIPTION_MAX_LENGTH,
    DESCRIPTION_MIN_LENGTH,
    EC_CURVE_LENGTH,
    KEY_IDENTIFIER_LENGTH,
    MAX_IDENTITY_LENGTH,
    MIN_IDENTITY_LENGTH,
)
from auth_server.models.database_enums import (
    ADMIN_ROLES,
    INBOX_ERROR_TYPES,
    INBOX_EVENT_SUBTYPES,
    INBOX_EVENT_TYPES,
    InboxErrorTypes,
)
from auth_server.models.events.enums import EventSubtype, EventType


class Base(DeclarativeBase):
    pass


class Admin(Base):
    __tablename__ = "admins"

    id_: Mapped[int] = mapped_column(INTEGER, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(
        VARCHAR(MAX_IDENTITY_LENGTH), nullable=False, unique=True
    )
    role: Mapped[AdminRole] = mapped_column(ADMIN_ROLES, nullable=False)

    password_hash: Mapped[bytes] = mapped_column(BYTEA, nullable=False)

    time_deleted: Mapped[datetime.datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("null"), nullable=True
    )
    last_login: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    locked: Mapped[bool] = mapped_column(
        BOOLEAN, nullable=False, server_default=text("false")
    )
    created_by: Mapped[int] = mapped_column(
        INTEGER, ForeignKey("admins.id_"), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            func.length(username) >= MIN_IDENTITY_LENGTH, "ck_username_min_length"
        ),
    )


class SuspiciousActivity(Base):
    __tablename__ = "suspicious_activities"

    id_: Mapped[int] = mapped_column(INTEGER, primary_key=True, autoincrement=True)
    suspect: Mapped[int] = mapped_column(
        INTEGER, ForeignKey("admins.id_"), nullable=False
    )
    time_logged: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    description: Mapped[str] = mapped_column(
        VARCHAR(DESCRIPTION_MAX_LENGTH), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            func.length(description) >= DESCRIPTION_MIN_LENGTH,
            "ck_suspicious_activities_description_length",
        ),
    )


class KeyData(Base):
    __tablename__ = "keydata"
    kid: Mapped[str] = mapped_column(VARCHAR(KEY_IDENTIFIER_LENGTH), primary_key=True)
    alg: Mapped[str] = mapped_column(
        VARCHAR(ALGORITHM_NAME_LEMGTH), nullable=False, server_default=text("'ES256'")
    )
    curve: Mapped[str] = mapped_column(VARCHAR(EC_CURVE_LENGTH), nullable=False)
    epoch: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        index=True,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    rotated_out_at: Mapped[datetime.datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("null"), nullable=True
    )
    expired_at: Mapped[datetime.datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("null"), nullable=True
    )
    private_pem: Mapped[bytes | None] = mapped_column(BYTEA, nullable=True)
    public_pem: Mapped[bytes] = mapped_column(BYTEA, nullable=False, unique=True)
    manual_rotation: Mapped[bool | None] = mapped_column(
        BOOLEAN, server_default=text("null"), nullable=True
    )
    rotated_by: Mapped[int | None] = mapped_column(
        INTEGER, ForeignKey("admins.id_"), index=True, nullable=True
    )

    __table_args__ = (
        Index(
            "uq_non_null_private_pem",
            private_pem,
            unique=True,
            postgresql_where=private_pem.isnot(None),
        ),
        CheckConstraint(
            (
                (private_pem.is_(None) & (rotated_out_at.is_not(None)))
                | (private_pem.is_not(None) & (rotated_out_at.is_(None)))
            ),
            "ck_private_pem_nullability",
        ),
        CheckConstraint(
            (rotated_out_at.is_(None)) | (rotated_out_at >= epoch),
            name="ck_rotation_after_creation",
        ),
        CheckConstraint(
            (expired_at.is_(None))
            | (rotated_out_at.is_not(None) & (expired_at >= rotated_out_at)),
            name="ck_expiry_after_rotation",
        ),
    )


class InboxTable(TransactionalBoxMixin, Base):
    __tablename__ = "event_inbox"

    # Override with narrowed EventType StrEnum
    event_type: Mapped[EventType] = mapped_column(INBOX_EVENT_TYPES, nullable=False)

    @declared_attr.directive
    @classmethod
    def __table_args__(cls) -> t_table_args:  # pyrefly: ignore[bad-override]
        return cls.table_constraints()


class EventDelivery(RetriedProcessingMixin, Base):
    __tablename__ = "event_deliveries"
    message_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey(f"{InboxTable.__tablename__}.{InboxTable.message_id.name}"),
        nullable=False,
    )
    subscriber_type: Mapped[EventSubtype] = mapped_column(
        INBOX_EVENT_SUBTYPES, nullable=False
    )

    @classmethod
    def implementation_constraints(cls) -> t_table_args:
        return (
            PrimaryKeyConstraint(cls.subscriber_type, cls.message_id),
            Index(
                "ix_pending_inbox_id",
                cls.subscriber_type,
                cls.message_id,
                postgresql_where=(cls.processed_at.is_(None)),
            ),
            Index(
                "ix_processed",
                cls.message_id,
                postgresql_where=(cls.processed_at.isnot(None)),
            ),
        )

    @declared_attr.directive
    @classmethod
    def __table_args__(cls) -> t_table_args:  # pyrefly: ignore[bad-override]
        return cls.implementation_constraints() + cls.constraints()


# class SubEventScheduleTrack(EventSchedulerMixin, Base):
#     """
#     Singleton table to keep track of the last sub-event in an inbox/outbox table
#     that was scheduled for processing
#     """
#
#     __tablename__ = "sub_event_schedule_track"
#
#     # Override with narrowed EventSubtype StrEnum
#     subscriber_type: Mapped[EventSubtype] = mapped_column(
#         INBOX_EVENT_SUBTYPES, primary_key=True
#     )
#
#     @declared_attr.directive
#     @classmethod
#     def __table_args__(cls) -> t_table_args:  # pyrefly: ignore[bad-override]
#         return cls.constraints()


class InboxDeadLetter(TransactionalBoxDeadLetterMixin, Base):
    __tablename__ = "inbox_dead_letter"

    # Override with narrowed InboxErrorTypes StrEnum
    error_type: Mapped[InboxErrorTypes] = mapped_column(
        INBOX_ERROR_TYPES,
        nullable=False,
        server_default=InboxErrorTypes.GENERAL_PROCESSING_ERROR,
    )

    @declared_attr.directive
    @classmethod
    def __table_args__(cls) -> t_table_args:  # pyrefly: ignore[bad-override]
        # Composite index on last_attempt to sort by ascending order
        implementation_constraints: t_table_args = (
            Index(
                "ix_event_type",
                cls.event_type,
                cls.last_attempt,
                postgresql_where=(cls.event_type.isnot(None)),
            ),
            Index("ix_error_type", cls.error_type, cls.last_attempt),
        )
        return implementation_constraints + cls.table_constraints()
