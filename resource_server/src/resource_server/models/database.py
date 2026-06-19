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

from resource_auxillary.events import (
    COUNTERS_DLQ_AFFECTED_COLUMN_COLUMN_NAME,
    COUNTERS_DLQ_AFFECTED_RELATION_COLUMN_NAME,
    COUNTERS_DLQ_FAILURE_TIME_COLUMN_NAME,
    COUNTERS_DLQ_TABLE_NAME,
    EVENTS_TABLE_NAME,
    EVENT_ID_COLUMN_NAME,
    EVENT_TIMESTAMP_COLUMN_NAME,
    DLQ_TABLE_NAME,
    DLQ_PAYLOAD_COLUMN_NAME,
)

from resource_server.config import database_constants
from resource_server.config.constants import EMAIL_PATTERN
from resource_server.models.database_enums import AdminRoles, ReportTags

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
class ForumSubscription(Base):
    __tablename__ = "forum_subscriptions"
    user_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("users.id_", ondelete="CASCADE"), primary_key=True
    )
    forum_id: Mapped[int] = mapped_column(
        INTEGER, ForeignKey("forums.id_", ondelete="CASCADE"), primary_key=True
    )
    time_subscribed: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class AnimeSubscription(Base):
    __tablename__ = "anime_subscriptions"
    user_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("users.id_", ondelete="CASCADE"), primary_key=True
    )
    anime_id: Mapped[int] = mapped_column(
        INTEGER, ForeignKey("animes.id_", ondelete="CASCADE"), primary_key=True
    )
    time_subscribed: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class PostVote(Base):
    __tablename__ = "post_votes"
    voter_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("users.id_"), primary_key=True
    )
    post_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("posts.id_", ondelete="CASCADE"), primary_key=True
    )
    vote: Mapped[bool] = mapped_column(BOOLEAN, nullable=False)


class PostSave(Base):
    __tablename__ = "post_saves"
    user_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("users.id_", ondelete="CASCADE"), primary_key=True
    )
    post_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("posts.id_", ondelete="CASCADE"), primary_key=True
    )


class PostReport(Base):
    __tablename__ = "post_reports"
    user_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("users.id_", ondelete="CASCADE"), primary_key=True
    )
    post_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("posts.id_", ondelete="CASCADE"), primary_key=True
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
        BIGINT, ForeignKey("users.id_", ondelete="CASCADE"), primary_key=True
    )
    comment_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey("comments.id_", ondelete="CASCADE"),
        primary_key=True,
    )
    report_tag: Mapped[str] = mapped_column(
        REPORT_TAGS, nullable=False, primary_key=True
    )
    report_time: Mapped[datetime] = mapped_column(
        TIMESTAMP, default=text("CURRENT_TIMESTAMP")
    )
    report_description: Mapped[str] = mapped_column(VARCHAR(256), nullable=False)

    def __json_like__(self) -> dict[str, int | str]:
        return {
            "user": self.user_id,
            "comment": self.comment_id,
            "tag": self.report_tag,
            "description": self.report_description,
            "time": self.report_time.isoformat(),
        }


class CommentVote(Base):
    __tablename__ = "comment_votes"
    voter_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("users.id_"), primary_key=True
    )
    comment_id: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey("comments.id_", ondelete="CASCADE"),
        primary_key=True,
    )
    vote: Mapped[bool] = mapped_column(BOOLEAN, nullable=False)


class StreamLink(Base):
    __tablename__ = "stream_links"
    anime_id: Mapped[int] = mapped_column(
        INTEGER, ForeignKey("animes.id_", ondelete="CASCADE"), primary_key=True
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
        INTEGER, ForeignKey("animes.id_", ondelete="CASCADE"), primary_key=True
    )
    genre_id: Mapped[int] = mapped_column(
        SMALLINT,
        ForeignKey("genres.id_", ondelete="CASCADE"),
        primary_key=True,
    )


ADMIN_ROLES = ENUM("admin", "super", "owner", name="ADMIN_ROLES", create_type=True)


class ForumAdmin(Base):
    __tablename__ = "forum_admins"
    forum_id: Mapped[int] = mapped_column(
        INTEGER, ForeignKey("forums.id_", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("users.id_", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(
        ADMIN_ROLES, nullable=False, server_default=text(f"'{ADMIN_ROLES.enums[0]}'")
    )


class User(Base):
    __tablename__ = "users"

    ### Attributes ###
    # Basic identification
    id_: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
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

    # Passwords and salts
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

    deleted: Mapped[bool] = mapped_column(
        BOOLEAN, nullable=False, server_default=text("false")
    )
    time_deleted: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=True)

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

    def __json_like__(self) -> dict[str, str | int]:
        return {
            "id": self.id_,
            "username": self.username,
            "aura": self.aura,
            "total_posts": self.total_posts,
            "total_comments": self.total_comments,
            "epoch": self.time_joined.isoformat(),
            "last_login": self.last_login.isoformat(),
        }


@dataclass
class UserTicket(Base):
    __tablename__ = "user_tickets"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
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
        BIGINT, ForeignKey("users.id_"), primary_key=True
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
    __tablename__ = "animes"

    id_: Mapped[int] = mapped_column(INTEGER, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(
        VARCHAR(database_constants.AnimeConstants.TITLE_MAX_LENGTH),
        nullable=False,
        unique=True,
    )

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

    @classmethod
    def deserialization_mapping(cls):
        return {
            "id": int,
            "rating": float,
            "mal_rating": int,
            "members": int,
            "genres": lambda x: x.split(":"),
        }

    def __json_like__(self) -> dict[str, str | int | float | None]:
        return {
            "id": self.id_,
            "title": self.title,
            "rating": float(self.rating),
            "mal_rating": self.mal_rating,
            "members": self.members,
            "synopsis": self.synopsis,
        }


@dataclass
class Genre(Base):
    __tablename__ = "genres"

    id_: Mapped[int] = mapped_column(SMALLINT, primary_key=True, autoincrement=True)
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


class Forum(Base):
    __tablename__ = "forums"

    # Basic identification
    id_: Mapped[int] = mapped_column(INTEGER, primary_key=True, autoincrement=True)
    name_: Mapped[str] = mapped_column(
        VARCHAR(database_constants.ForumConstants.TITLE_MAX_LENGTH), nullable=False
    )
    anime: Mapped[int] = mapped_column(
        INTEGER, ForeignKey("animes.id_"), index=True, nullable=True
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

    # Deletion metadata
    deleted: Mapped[bool] = mapped_column(
        BOOLEAN, nullable=False, server_default=text("false")
    )
    time_deleted: Mapped[datetime | None] = mapped_column(TIMESTAMP)
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
        UniqueConstraint("name_", "anime", name="uq_name_anime"),
    )

    @classmethod
    def deserialization_mapping(cls) -> dict[str, Any]:
        hp_func = lambda hp: None if not hp else int(hp)
        return {
            "id": int,
            "anime": int,
            "subscribers": int,
            "posts": int,
            "admin_count": int,
            "highlight_post_1": hp_func,
            "highlight_post_2": hp_func,
            "highlight_post_3": hp_func,
            "deleted": lambda deleted: bool(int(deleted)),
            "rtbf_hidden": lambda rtbf: bool(int(rtbf)),
        }

    def __json_like__(self) -> dict[str, str | int | None]:
        return {
            "id": self.id_,
            "name": self.name_,
            "subscribers": self.subscribers,
            "description": self.description,
            "posts": self.posts,
            "created_at": self.created_at.isoformat(),
            "admin_count": self.admin_count,
        }


class ForumRules(Base):
    __tablename__ = "forum_rules"

    # Identification
    forum_id: Mapped[int] = mapped_column(
        INTEGER, ForeignKey("forums.id_", ondelete="CASCADE"), primary_key=True
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
        INTEGER, ForeignKey("users.id_"), nullable=False
    )

    time_created: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False)

    __table_args__ = (
        CheckConstraint(
            and_(rule_number >= 1, rule_number <= 5), "enforce_forum_rules_range"
        ),
    )

    def __json_like__(self) -> dict:
        return {
            "forum_id": self.forum_id,
            "rule_number": self.rule_number,
            "title": self.title,
            "body": self.body,
            "author": self.author,
            "epoch": self.time_created.isoformat(),
        }


class Post(Base):
    __tablename__ = "posts"

    # Basic identification
    id_: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    author_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("users.id_"), nullable=False, index=True
    )
    forum_id: Mapped[int] = mapped_column(
        INTEGER, ForeignKey("forums.id_", ondelete="CASCADE"), nullable=False
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
    time_deleted: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    rtbf_hidden: Mapped[bool] = mapped_column(BOOLEAN, nullable=True)

    @classmethod
    def deserialization_mapping(cls) -> dict[str, Any]:
        return {
            "id": int,
            "author_id": int,
            "forum_id": int,
            "score": int,
            "total_comments": int,
            "closed": deserialize_bool,
            "saves": int,
            "reports": int,
            "deleted": deserialize_bool,
            "rtbf_hidden": deserialize_bool,
        }

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

    def __json_like__(self) -> dict[str, str | int | None]:
        return {
            "id": self.id_,
            "author_id": self.author_id,
            "forum_id": self.forum_id,
            "score": self.score,
            "total_comments": self.total_comments,
            "title": self.title,
            "body_text": self.body_text,
            "closed": self.closed,
            "epoch": self.time_posted.strftime("%d/%m/%y, %H:%M:%S"),
            "saves": self.saves,
        }


class Comment(Base):
    __tablename__ = "comments"

    # Basic identification
    id_: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    author_id: Mapped[int] = mapped_column(
        BIGINT, ForeignKey("users.id_"), nullable=False, index=True
    )
    parent_forum: Mapped[int] = mapped_column(
        INTEGER, ForeignKey("forums.id_", ondelete="CASCADE"), nullable=False
    )

    # Comment details
    time_created: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    body: Mapped[str] = mapped_column(
        VARCHAR(database_constants.CommentConstants.COMMENT_MAX_LENGTH), nullable=False
    )
    parent_post: Mapped[int] = mapped_column(
        BIGINT,
        ForeignKey("posts.id_", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    score: Mapped[int] = mapped_column(
        BIGINT, nullable=False, default=0, server_default=text("0")
    )
    reports: Mapped[int] = mapped_column(
        INTEGER, default=0, server_default=text("0"), nullable=False
    )

    # Deletion metadata
    deleted: Mapped[bool] = mapped_column(
        BOOLEAN, nullable=False, server_default=text("false")
    )
    time_deleted: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    rtbf_hidden: Mapped[bool] = mapped_column(BOOLEAN, nullable=True)

    __table_args__ = (
        CheckConstraint(reports > 0, "check_reports_value"),
        CheckConstraint(
            func.length(body) >= database_constants.CommentConstants.COMMENT_MIN_LENGTH,
            "check_comment_length",
        ),
    )

    def __json_like__(self) -> dict[str, str | int]:
        return {
            "id": self.id_,
            "author_id": self.author_id,
            "parent_forum": self.parent_forum,
            "time_created": self.time_created.isoformat(),
            "body": self.body,
            "parent_post": self.parent_post,
            "score": self.score,
        }


class StreamEvent(Base):
    __tablename__ = EVENTS_TABLE_NAME

    event_id: Mapped[str] = mapped_column(
        TEXT, primary_key=True, name=EVENT_ID_COLUMN_NAME
    )
    acknowledgement_time: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
        name=EVENT_TIMESTAMP_COLUMN_NAME,
    )


class DeadLetterQueue(Base):
    __tablename__ = DLQ_TABLE_NAME

    event_id: Mapped[str] = mapped_column(
        TEXT, primary_key=True, name=EVENT_ID_COLUMN_NAME
    )
    payload: Mapped[Any] = mapped_column(
        JSONB, nullable=False, name=DLQ_PAYLOAD_COLUMN_NAME
    )


class CounterDeadLetterQueue(Base):
    __tablename__ = COUNTERS_DLQ_TABLE_NAME

    id_: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)

    table_name: Mapped[str] = mapped_column(
        VARCHAR(64),
        index=True,
        nullable=False,
        name=COUNTERS_DLQ_AFFECTED_RELATION_COLUMN_NAME,
    )

    column_name: Mapped[str] = mapped_column(
        VARCHAR(64),
        index=True,
        nullable=False,
        name=COUNTERS_DLQ_AFFECTED_COLUMN_COLUMN_NAME,
    )

    failure_time: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        index=True,
        server_default=text("CURRENT_TIMESTAMP"),
        name=COUNTERS_DLQ_FAILURE_TIME_COLUMN_NAME,
    )

    counter_data: Mapped[Any] = mapped_column(
        JSONB, nullable=False, name=DLQ_PAYLOAD_COLUMN_NAME
    )
