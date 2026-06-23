from sqlalchemy import (
    ForeignKey,
    CheckConstraint,
    UniqueConstraint,
    and_,
    or_,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import text, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, BYTEA, ENUM
from sqlalchemy.types import INTEGER, SMALLINT, BOOLEAN, VARCHAR, BIGINT, NUMERIC, TEXT

from datetime import datetime
from typing import Any
from dataclasses import dataclass
from types import FunctionType

from resource_auxillary.datastructures.database import (
    StrongEntity,
    EventLiteral,
    DeadLetterQueueLiteral,
    ForeignKeyColumnLiteral,
    AssociationColumnLiteral,
    GenericLiterals,
)

from resource_server.config import database_constants
from resource_server.config.constants import EMAIL_PATTERN
from resource_server.models.database_enums import AdminRoles, ReportTags
from resource_server.models.database_mixins import (
    SaveAssociationMixin,
    SoftDeletionMixin,
    SoftEventDeletionMixin,
    SubAssociationMixin,
    VoteAssociationMixin,
)

__all__ = (
    "ForumSubscription",
    "AnimeSubscription",
    "PostVote",
    "PostSave",
    "PostReport",
    "CommentReport",
    "CommentVote",
    "StreamLink",
    "AnimeGenre",
    "ForumAdmin",
    "User",
    "UserTicket",
    "PasswordRecoveryToken",
    "Anime",
    "Genre",
    "Forum",
    "ForumRules",
    "Post",
    "Comment",
)


class Base(DeclarativeBase):
    pass


### Deserialization functions commonly used in all models
deserialize_bool: FunctionType = lambda serial: bool(int(serial))
deserialize_optional: FunctionType = lambda serial: None if not serial else serial

### Enums ###
ADMIN_ROLES = ENUM(*(i.value for i in AdminRoles), name="ADMIN_ROLES", create_type=True)

REPORT_TAGS = ENUM(
    *(i.value for i in ReportTags),
    name="REPORT_TAGS",
    create_type=True,
)


### Assosciation Tables ###
class ForumSubscription(SubAssociationMixin, Base):
    __tablename__ = "forum_subscriptions"
    user_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey(f"{StrongEntity.USER}.{GenericLiterals.ID}", ondelete="CASCADE"),
        primary_key=True,
        name=AssociationColumnLiteral.USER_ID,
    )
    forum_id: Mapped[int] = mapped_column(
        INTEGER,
        ForeignKey(f"{StrongEntity.FORUM}.{GenericLiterals.ID}", ondelete="CASCADE"),
        primary_key=True,
        name=AssociationColumnLiteral.FORUM_ID,
    )
    time_subscribed: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class AnimeSubscription(SubAssociationMixin, Base):
    __tablename__ = "anime_subscriptions"
    user_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey(f"{StrongEntity.USER}.{GenericLiterals.ID}", ondelete="CASCADE"),
        primary_key=True,
        name=AssociationColumnLiteral.USER_ID,
    )
    anime_id: Mapped[int] = mapped_column(
        INTEGER,
        ForeignKey(f"{StrongEntity.ANIME}.{GenericLiterals.ID}", ondelete="CASCADE"),
        primary_key=True,
        name=AssociationColumnLiteral.ANIME_ID,
    )
    time_subscribed: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class PostVote(VoteAssociationMixin, Base):
    __tablename__ = "post_votes"
    voter_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey(f"{StrongEntity.USER}.{GenericLiterals.ID}"),
        primary_key=True,
        name=AssociationColumnLiteral.USER_ID,
    )
    post_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey(f"{StrongEntity.POST}.{GenericLiterals.ID}", ondelete="CASCADE"),
        primary_key=True,
        name=AssociationColumnLiteral.POST_ID,
    )


class PostSave(SaveAssociationMixin, Base):
    __tablename__ = "post_saves"
    user_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey(f"{StrongEntity.USER}.{GenericLiterals.ID}", ondelete="CASCADE"),
        primary_key=True,
        name=AssociationColumnLiteral.USER_ID,
    )
    post_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey(f"{StrongEntity.POST}.{GenericLiterals.ID}", ondelete="CASCADE"),
        primary_key=True,
        name=AssociationColumnLiteral.POST_ID,
    )


class PostReport(Base):
    __tablename__ = "post_reports"
    user_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey(f"{StrongEntity.USER}.{GenericLiterals.ID}", ondelete="CASCADE"),
        primary_key=True,
        name=AssociationColumnLiteral.USER_ID,
    )
    post_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey(f"{StrongEntity.POST}.{GenericLiterals.ID}", ondelete="CASCADE"),
        primary_key=True,
        name=AssociationColumnLiteral.POST_ID,
    )
    report_tag: Mapped[str] = mapped_column(
        REPORT_TAGS, nullable=False, primary_key=True
    )
    report_time: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=text("CURRENT_TIMESTAMP")
    )
    report_description: Mapped[str] = mapped_column(
        VARCHAR(database_constants.PostConstants.REPORT_DESCRIPTION_MAX_LENGTH),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            func.length(report_description)
            <= database_constants.PostConstants.REPORT_DESCRIPTION_MIN_LENGTH
        ),
    )


class CommentReport(Base):
    __tablename__ = "comment_reports"
    user_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey(f"{StrongEntity.USER}.{GenericLiterals.ID}", ondelete="CASCADE"),
        primary_key=True,
        name=AssociationColumnLiteral.USER_ID,
    )
    comment_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey("comments.id_", ondelete="CASCADE"),
        primary_key=True,
        name=AssociationColumnLiteral.COMMENT_ID,
    )
    report_tag: Mapped[str] = mapped_column(
        REPORT_TAGS, nullable=False, primary_key=True
    )
    report_time: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=text("CURRENT_TIMESTAMP")
    )
    report_description: Mapped[str] = mapped_column(VARCHAR(256), nullable=False)


class CommentVote(VoteAssociationMixin, Base):
    __tablename__ = "comment_votes"
    voter_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey(f"{StrongEntity.USER}.{GenericLiterals.ID}"),
        primary_key=True,
        name=AssociationColumnLiteral.USER_ID,
    )
    comment_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey("comments.id_", ondelete="CASCADE"),
        primary_key=True,
        name=AssociationColumnLiteral.COMMENT_ID,
    )


class StreamLink(Base):
    __tablename__ = "stream_links"
    anime_id: Mapped[int] = mapped_column(
        INTEGER,
        ForeignKey(f"{StrongEntity.ANIME}.{GenericLiterals.ID}", ondelete="CASCADE"),
        primary_key=True,
        name=AssociationColumnLiteral.ANIME_ID,
    )
    url: Mapped[str] = mapped_column(
        VARCHAR(database_constants.StreamLinkConstants.URL_MAX_LENGTH),
        primary_key=True,
        nullable=False,
    )
    website: Mapped[str] = mapped_column(
        VARCHAR(database_constants.StreamLinkConstants.URL_MAX_LENGTH), nullable=False
    )


class AnimeGenre(Base):
    __tablename__ = "anime_genres"
    anime_id: Mapped[int] = mapped_column(
        INTEGER,
        ForeignKey(f"{StrongEntity.ANIME}.{GenericLiterals.ID}", ondelete="CASCADE"),
        primary_key=True,
        name=AssociationColumnLiteral.ANIME_ID,
    )
    genre_id: Mapped[int] = mapped_column(
        SMALLINT,
        ForeignKey("genres.id_", ondelete="CASCADE"),
        primary_key=True,
        name=AssociationColumnLiteral.GENRE_ID,
    )


class ForumAdmin(Base):
    __tablename__ = "forum_admins"
    forum_id: Mapped[int] = mapped_column(
        INTEGER,
        ForeignKey(f"{StrongEntity.FORUM}.{GenericLiterals.ID}", ondelete="CASCADE"),
        primary_key=True,
        name=AssociationColumnLiteral.FORUM_ID,
    )
    user_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey(f"{StrongEntity.USER}.{GenericLiterals.ID}", ondelete="CASCADE"),
        primary_key=True,
        name=AssociationColumnLiteral.USER_ID,
    )
    role: Mapped[str] = mapped_column(
        ADMIN_ROLES, nullable=False, server_default=text(AdminRoles.ADMIN)
    )


class User(SoftDeletionMixin, Base):
    __tablename__ = StrongEntity.USER

    # Basic identification
    id_: Mapped[int] = mapped_column(
        BIGINT, primary_key=True, autoincrement=True, name=GenericLiterals.ID
    )
    username: Mapped[str] = mapped_column(
        VARCHAR(database_constants.UserConstants.USERNAME_MAX_LENGTH),
        nullable=False,
        unique=True,
    )
    email: Mapped[str] = mapped_column(
        VARCHAR(database_constants.UserConstants.EMAIL_MAX_LENGTH),
        nullable=False,
        unique=True,
    )
    rtbf: Mapped[bool] = mapped_column(
        BOOLEAN, server_default=text("false"), nullable=False
    )

    # bcrypt password hash
    pw_hash: Mapped[bytes] = mapped_column(
        BYTEA(database_constants.UserConstants.PASSWORD_HASH_LENGTH), nullable=False
    )

    # Activity
    aura: Mapped[int] = mapped_column(BIGINT, default=0, server_default=text("0"))
    total_posts: Mapped[int] = mapped_column(
        INTEGER, default=0, server_default=text("0")
    )
    total_comments: Mapped[int] = mapped_column(
        INTEGER, default=0, server_default=text("0")
    )
    time_joined: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    last_login: Mapped[datetime] = mapped_column(TIMESTAMP)

    __table_args__ = (
        CheckConstraint(
            func.length(username)
            >= database_constants.UserConstants.USERNAME_MIN_LENGTH,
            name="ck_users_username_length",
        ),
        CheckConstraint(
            func.length(email) >= database_constants.UserConstants.EMAIL_MIN_LENGTH
        ),
        CheckConstraint(email.regexp_match(str(EMAIL_PATTERN)), "check_email_regex"),
    )


@dataclass
class UserTicket(Base):
    __tablename__ = "user_tickets"

    id_: Mapped[int] = mapped_column(BIGINT, primary_key=True, name=GenericLiterals.ID)
    email: Mapped[str] = mapped_column(
        VARCHAR(database_constants.UserConstants.EMAIL_MAX_LENGTH), nullable=False
    )
    time_raised: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    description: Mapped[str] = mapped_column(
        VARCHAR(database_constants.UserTicketConstants.DESCRIPTION_MAX_LENGTH),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            func.length(description)
            >= database_constants.UserTicketConstants.DESCRIPTION_MIN_LENGTH
        ),
    )


@dataclass
class PasswordRecoveryToken(Base):
    __tablename__ = "password_recovery_tokens"

    user_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey(f"{StrongEntity.USER}.{GenericLiterals.ID}"),
        primary_key=True,
    )
    expiry: Mapped[datetime] = mapped_column(
        TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"), nullable=False, index=True
    )
    url_hash: Mapped[str] = mapped_column(
        VARCHAR(database_constants.PasswordRecoveryConstants.URL_HASH_LENGTH),
        nullable=False,
        unique=True,
    )


@dataclass
class Anime(Base):
    __tablename__ = StrongEntity.ANIME

    id_: Mapped[int] = mapped_column(
        INTEGER, primary_key=True, autoincrement=True, name=GenericLiterals.ID
    )
    title: Mapped[str] = mapped_column(
        VARCHAR(database_constants.AnimeConstants.TITLE_MAX_LENGTH),
        nullable=False,
        unique=True,
    )

    # NOTE: what
    rating: Mapped[float] = mapped_column(NUMERIC(3, 2), nullable=False)
    mal_rating: Mapped[int | None] = mapped_column(INTEGER)
    members: Mapped[int] = mapped_column(
        BIGINT, nullable=False, server_default=text("0")
    )
    synopsis: Mapped[str] = mapped_column(
        VARCHAR(database_constants.AnimeConstants.DESCRIPTION_MAX_LENGTH),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(mal_rating >= 0, "check_ranking_positive"),
        CheckConstraint(members >= 0, "check_members_positive"),
        CheckConstraint(rating >= 0, "check_rating_positive"),
        CheckConstraint(
            rating <= database_constants.AnimeConstants.HIGHEST_MAL_RATING,
            "check_rating_upperbound",
        ),
    )


@dataclass
class Genre(Base):
    __tablename__ = StrongEntity.GENRE

    id_: Mapped[int] = mapped_column(
        SMALLINT, primary_key=True, autoincrement=True, name=GenericLiterals.ID
    )
    name_: Mapped[str] = mapped_column(
        VARCHAR(database_constants.GenreConstants.TITLE_MAX_LENGTH),
        nullable=False,
        unique=True,
    )

    __table_args__ = (
        CheckConstraint(
            func.length(name_) > database_constants.GenreConstants.TITLE_MIN_LENGTH
        ),
    )


class Forum(SoftEventDeletionMixin, Base):
    __tablename__ = StrongEntity.FORUM

    # Basic identification
    id_: Mapped[int] = mapped_column(
        INTEGER, primary_key=True, autoincrement=True, name=GenericLiterals.ID
    )
    name_: Mapped[str] = mapped_column(
        VARCHAR(database_constants.ForumConstants.TITLE_MAX_LENGTH), nullable=False
    )
    anime: Mapped[int] = mapped_column(
        INTEGER,
        ForeignKey(f"{StrongEntity.ANIME}.{GenericLiterals.ID}"),
        index=True,
        nullable=True,
        name=ForeignKeyColumnLiteral.PARENT_ANIME,
    )

    # Appearance
    description: Mapped[str | None] = mapped_column(
        VARCHAR(database_constants.ForumConstants.DESCRIPTION_MAX_LENGTH)
    )

    # Activity stats
    subscribers: Mapped[int] = mapped_column(
        BIGINT, nullable=False, default=0, server_default=text("0")
    )
    posts: Mapped[int] = mapped_column(
        BIGINT, nullable=False, default=0, server_default=text("0")
    )

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)
    admin_count: Mapped[int] = mapped_column(
        SMALLINT, default=1, server_default=text("1"), nullable=False
    )

    rtbf_hidden: Mapped[bool] = mapped_column(BOOLEAN)

    __table_args__ = (
        CheckConstraint(posts >= 0, name="check_posts_value"),
        CheckConstraint(subscribers >= 0, name="check_subs_values"),
        CheckConstraint(admin_count > 0, name="check_atleast_1_admin"),
        CheckConstraint(
            or_(
                func.length(description)
                > database_constants.ForumConstants.DESCRIPTION_MIN_LENGTH,
                description.is_(None),
            )
        ),
        CheckConstraint(
            func.length(name_) >= database_constants.ForumConstants.TITLE_MIN_LENGTH,
            name="check_title_length",
        ),
        UniqueConstraint("name_", name="uq_forum_name"),
    )


class ForumRules(Base):
    __tablename__ = "forum_rules"

    # Identification
    forum_id: Mapped[int] = mapped_column(
        INTEGER,
        ForeignKey(f"{StrongEntity.FORUM}.{GenericLiterals.ID}", ondelete="CASCADE"),
        primary_key=True,
        name=AssociationColumnLiteral.FORUM_ID,
    )

    # Data
    rule_number: Mapped[int] = mapped_column(SMALLINT, primary_key=True, unique=True)
    title: Mapped[str] = mapped_column(
        VARCHAR(database_constants.ForumConstants.RULE_TITLE_MAX_LENGTH), nullable=False
    )
    body: Mapped[str] = mapped_column(
        VARCHAR(database_constants.ForumConstants.RULE_DESCRIPTION_MAX_LENGTH),
        server_default="No additional description provided for this rule.",
    )
    author: Mapped[int] = mapped_column(
        INTEGER, ForeignKey(f"{StrongEntity.USER}.{GenericLiterals.ID}"), nullable=False
    )

    time_created: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)

    __table_args__ = (
        CheckConstraint(
            and_(rule_number >= 1, rule_number <= 5), "enforce_forum_rules_range"
        ),
    )


class Post(SoftEventDeletionMixin, Base):
    __tablename__ = StrongEntity.POST

    # Basic identification
    id_: Mapped[int] = mapped_column(
        BIGINT, primary_key=True, autoincrement=True, name=GenericLiterals.ID
    )
    author_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey(f"{StrongEntity.USER}.{GenericLiterals.ID}"),
        nullable=False,
        index=True,
        name=ForeignKeyColumnLiteral.AUTHOR_ID,
    )
    forum_id: Mapped[int] = mapped_column(
        INTEGER,
        ForeignKey(f"{StrongEntity.FORUM}.{GenericLiterals.ID}", ondelete="CASCADE"),
        nullable=False,
        name=ForeignKeyColumnLiteral.PARENT_FORUM,
    )

    # Post statistics
    score: Mapped[int] = mapped_column(
        INTEGER, default=0, server_default=text("0"), nullable=False
    )
    total_comments: Mapped[int] = mapped_column(
        INTEGER, default=0, server_default=text("0"), nullable=False
    )

    # Post details
    title: Mapped[str] = mapped_column(
        VARCHAR(database_constants.PostConstants.TITLE_MAX_LENGTH),
        nullable=False,
        index=True,
    )

    body_text: Mapped[str | None] = mapped_column(
        VARCHAR(database_constants.PostConstants.BODY_MAX_LENGTH)
    )

    # TODO: Add assosciation table for forum and post flairs
    flair: Mapped[str | None] = mapped_column(VARCHAR(16), index=True)
    closed: Mapped[bool] = mapped_column(
        BOOLEAN, default=False, server_default=text("false")
    )
    time_posted: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    saves: Mapped[int] = mapped_column(
        INTEGER, default=0, server_default=text("0"), nullable=False
    )
    reports: Mapped[int] = mapped_column(
        INTEGER, default=0, server_default=text("0"), nullable=False
    )

    # Deletion metadata
    deleted: Mapped[bool] = mapped_column(
        BOOLEAN, nullable=False, server_default=text("false")
    )

    rtbf_hidden: Mapped[bool] = mapped_column(BOOLEAN, nullable=True)

    __table_args__ = (
        CheckConstraint(
            func.length(title) >= database_constants.PostConstants.TITLE_MIN_LENGTH,
            name="check_title_min_length",
        ),
        CheckConstraint(
            or_(
                func.length(body_text)
                <= database_constants.PostConstants.BODY_MAX_LENGTH,
                body_text.is_(None),
            ),
            name="check_body_text",
        ),
    )


class Comment(SoftEventDeletionMixin, Base):
    __tablename__ = StrongEntity.COMMENT

    # Basic identification
    id_: Mapped[int] = mapped_column(
        BIGINT, primary_key=True, autoincrement=True, name=GenericLiterals.ID
    )
    author_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey(f"{StrongEntity.USER}.{GenericLiterals.ID}"),
        nullable=False,
        index=True,
        name=ForeignKeyColumnLiteral.AUTHOR_ID,
    )
    parent_forum: Mapped[int] = mapped_column(
        INTEGER,
        ForeignKey(f"{StrongEntity.FORUM}.{GenericLiterals.ID}", ondelete="CASCADE"),
        nullable=False,
        name=ForeignKeyColumnLiteral.PARENT_FORUM,
    )
    parent_post: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey(f"{StrongEntity.POST}.{GenericLiterals.ID}", ondelete="CASCADE"),
        nullable=False,
        index=True,
        name=ForeignKeyColumnLiteral.PARENT_POST,
    )

    # Comment details
    time_created: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    body: Mapped[str] = mapped_column(
        VARCHAR(database_constants.CommentConstants.COMMENT_MAX_LENGTH), nullable=False
    )
    score: Mapped[int] = mapped_column(
        BIGINT, nullable=False, default=0, server_default=text("0")
    )
    reports: Mapped[int] = mapped_column(
        INTEGER, default=0, server_default=text("0"), nullable=False
    )

    rtbf_hidden: Mapped[bool] = mapped_column(BOOLEAN, nullable=True)

    __table_args__ = (
        CheckConstraint(reports > 0, "check_reports_value"),
        CheckConstraint(
            func.length(body) >= database_constants.CommentConstants.COMMENT_MIN_LENGTH,
            "check_comment_length",
        ),
    )


class StreamEvent(Base):
    __tablename__ = EventLiteral.EVENTS_TABLE_NAME

    event_id: Mapped[str] = mapped_column(
        TEXT, primary_key=True, name=EventLiteral.EVENT_ID_COLUMN_NAME
    )
    acknowledgement_time: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
        name=EventLiteral.EVENT_TIMESTAMP_COLUMN_NAME,
    )


class DeadLetterQueue(Base):
    __tablename__ = DeadLetterQueueLiteral.TABLE_NAME

    event_id: Mapped[str] = mapped_column(
        TEXT, primary_key=True, name=EventLiteral.EVENT_ID_COLUMN_NAME
    )
    payload: Mapped[Any] = mapped_column(
        JSONB, nullable=False, name=DeadLetterQueueLiteral.PAYLOAD_COLUMN_NAME
    )

    failure_time: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        index=True,
        server_default=text("CURRENT_TIMESTAMP"),
        name=DeadLetterQueueLiteral.COUNTERS_FAILURE_TIME_COLUMN_NAME,
    )


class CounterDeadLetterQueue(Base):
    __tablename__ = DeadLetterQueueLiteral.COUNTERS_TABLE_NAME

    id_: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)

    table_name: Mapped[str] = mapped_column(
        VARCHAR(64),
        index=True,
        nullable=False,
        name=DeadLetterQueueLiteral.COUNTERS_AFFECTED_RELATION_COLUMN_NAME,
    )

    column_name: Mapped[str] = mapped_column(
        VARCHAR(64),
        index=True,
        nullable=False,
        name=DeadLetterQueueLiteral.COUNTERS_AFFECTED_COLUMN_COLUMN_NAME,
    )

    failure_time: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        index=True,
        server_default=text("CURRENT_TIMESTAMP"),
        name=DeadLetterQueueLiteral.COUNTERS_FAILURE_TIME_COLUMN_NAME,
    )

    counter_data: Mapped[Any] = mapped_column(
        JSONB, nullable=False, name=DeadLetterQueueLiteral.PAYLOAD_COLUMN_NAME
    )
