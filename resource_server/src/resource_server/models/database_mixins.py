from sqlalchemy.orm import declared_attr
from datetime import datetime
from typing import Any

from sqlalchemy import text, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import BIGINT, BOOLEAN, TIMESTAMP, TEXT, JSONB

from resource_auxillary.datastructures.database import (
    DeletionColumnLiteral,
    EventMetadataLiteral,
    SideEffectsLiteral,
    EventLiteral,
)
from resource_auxillary.strings import EventName

from resource_server.models.database_enums import EVENT_NAME


class EventAssociationMixin:
    last_event_seq: Mapped[int] = mapped_column(
        BIGINT,
        nullable=False,
        name=EventMetadataLiteral.LAST_EVENT_IDENTIFIER_COLUMN_NAME,
    )


class SaveAssociationMixin(EventAssociationMixin):
    is_saved: Mapped[int] = mapped_column(
        BOOLEAN,
        nullable=False,
        server_default=text("true"),
        name=EventMetadataLiteral.EVENT_SAVE_COLUMN_NAME,
    )


class VoteAssociationMixin(EventAssociationMixin):
    vote_type: Mapped[bool] = mapped_column(
        BOOLEAN, name=EventMetadataLiteral.EVENT_VOTE_COLUMN_NAME
    )


class SubAssociationMixin(EventAssociationMixin):
    is_subscribed: Mapped[bool] = mapped_column(
        BOOLEAN,
        nullable=False,
        server_default=text("true"),
        name=EventMetadataLiteral.EVENT_SUB_COLUMN_NAME,
    )


class SoftDeletionMixin:
    deleted: Mapped[bool] = mapped_column(
        BOOLEAN,
        nullable=False,
        server_default=text("false"),
        name=DeletionColumnLiteral.DELETED_COLUMN_NAME,
    )
    time_deleted: Mapped[datetime | None] = mapped_column(
        TIMESTAMP, name=DeletionColumnLiteral.DELETION_TIME_COLUMN_NAME
    )


class SoftEventDeletionMixin(SoftDeletionMixin):
    deletion_author_event: Mapped[int | None] = mapped_column(
        BIGINT, name=DeletionColumnLiteral.DELETION_AUTHOR_EVENT
    )


class EventTableMixin:
    event_id: Mapped[str] = mapped_column(
        TEXT, primary_key=True, name=EventLiteral.EVENT_ID_COLUMN_NAME
    )
    event_name: Mapped[EventName] = mapped_column(
        EVENT_NAME, nullable=False, name=EventLiteral.EVENT_NAME_COLUMN_NAME, index=True
    )


class EventReferrerTableMixin:
    event_id: Mapped[str] = mapped_column(
        TEXT,
        ForeignKey(
            f"{EventLiteral.EVENTS_TABLE_NAME}.{EventLiteral.EVENT_ID_COLUMN_NAME}"
        ),
        primary_key=True,
        name=EventLiteral.EVENT_ID_COLUMN_NAME,
    )


class EventSideEffectsTableMixin(EventReferrerTableMixin):
    __tablename__: str
    side_effects_emitted: Mapped[bool] = mapped_column(
        BOOLEAN, nullable=False, name=SideEffectsLiteral.SIDE_EFFECTS_EMITTED
    )
    payload: Mapped[Any] = mapped_column(
        JSONB, name=SideEffectsLiteral.SIDE_EFFECTS_PAYLOAD
    )

    def __init_subclass__(cls) -> None:
        if not hasattr(cls, "__tablename__"):
            raise ValueError(f"Missing __tablename__ in class: {cls.__name__}")
        if not hasattr(cls.__tablename__, "__str__"):
            raise ValueError(
                f"String incompatible tablename provided: {cls.__tablename__}"
            )

    @declared_attr.directive
    def __table_args__(cls):
        return (
            Index(
                f"{cls.__tablename__}_{SideEffectsLiteral.NON_EMITTED_EVENTS_INDEX}",
                cls.event_id,
                postgresql_where=(
                    ~cls.side_effects_emitted
                ),  # pyrefly: ignore[deprecated]
            ),
        )
