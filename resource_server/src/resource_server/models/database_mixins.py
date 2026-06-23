from datetime import datetime

from resource_auxillary.datastructures.database import (
    DeletionColumnLiteral,
    EventMetadataLiteral,
)
from sqlalchemy import text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import BIGINT, BOOLEAN, TIMESTAMP


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
