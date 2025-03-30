from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

from sqlalchemy import PrimaryKeyConstraint, CheckConstraint, UniqueConstraint, MetaData
from sqlalchemy.sql import text, DDL
from sqlalchemy.orm import Mapped
from sqlalchemy.dialects.postgresql import TIMESTAMP, BYTEA, ENUM
from sqlalchemy.types import INTEGER, SMALLINT, BOOLEAN, VARCHAR, BIGINT, NUMERIC, TEXT

import orjson, os
from datetime import datetime

from dataclasses import dataclass

CONFIG : dict = {}
with open(os.path.join(os.path.dirname(__file__), "instance", "config.json"), 'rb') as configFile:
    CONFIG = orjson.loads(configFile.read())
    METADATA = MetaData(naming_convention=CONFIG["database"]["naming_convention"])

db = SQLAlchemy()

### Assosciation Tables ###
forum_subscriptions = db.Table(
    "forum_subscriptions",
    db.Column("user_id", BIGINT, db.ForeignKey("users.id")),
    db.Column("forum_id", INTEGER, db.ForeignKey("forums.id")),
    db.Column("time_subscribed", TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    db.PrimaryKeyConstraint("user_id", "forum_id", name="pk_forum_subscriptions")
)

anime_subscriptions = db.Table(
    "anime_subscriptions",
    db.Column("user_id", BIGINT, db.ForeignKey("users.id")),
    db.Column("anime_id", INTEGER, db.ForeignKey("animes.id")),
    db.PrimaryKeyConstraint("user_id", "anime_id", name="pk_anime_subscriptions")
)

comment_votes = db.Table(
    "comment_votes",
    db.Column("voter_id", BIGINT, db.ForeignKey("users.id")),
    db.Column("comment_id", BIGINT, db.ForeignKey("comments.id")),
    db.Column("vote", BOOLEAN, nullable=False),
    db.PrimaryKeyConstraint("voter_id", "comment_id", name="pk_comment_votes")
)

comment_reports = db.Table(
    "comment_reports",
    db.Column("user_id", BIGINT, db.ForeignKey("users.id")),
    db.Column("comment_id", BIGINT, db.ForeignKey("comments.id")),
    db.PrimaryKeyConstraint("user_id", "comment_id", name="pk_comment_reports")
)

post_votes = db.Table(
    "post_votes",
    db.Column("voter_id", BIGINT, db.ForeignKey("users.id")),
    db.Column("post_id", BIGINT, db.ForeignKey("posts.id")),
    db.Column("vote", BOOLEAN, nullable=False),
    db.PrimaryKeyConstraint("voter_id", "post_id", name="pk_post_votes")
)

post_saves = db.Table(
    "post_saves",
    db.Column("user_id", BIGINT, db.ForeignKey("users.id")),
    db.Column("post_id", BIGINT, db.ForeignKey("posts.id")),
    db.PrimaryKeyConstraint("user_id", "post_id", name="pk_post_saves")
)

post_reports = db.Table(
    "post_reports",
    db.Column("user_id", BIGINT, db.ForeignKey("users.id")),
    db.Column("post_id", BIGINT, db.ForeignKey("posts.id")),
    db.PrimaryKeyConstraint("user_id", "post_id", name="pk_post_reports")
)

stream_links = db.Table(
    "stream_links",
    db.Column("anime_id", INTEGER, db.ForeignKey("animes.id")),
    db.Column("url", VARCHAR(256), nullable = False),
    db.Column("website", VARCHAR(128), nullable = False),
    db.PrimaryKeyConstraint("anime_id", "url", name="pk_stream_links")
)

anime_genres = db.Table(
    "anime_genres",
    db.Column("anime_id", INTEGER, db.ForeignKey("animes.id")),
    db.Column("genre_id", SMALLINT, db.ForeignKey("genres.id")),
    db.PrimaryKeyConstraint("anime_id", "genre_id", name="pk_anime_genres")
)

ADMIN_ROLES = ENUM("admin", "super", "owner", name="ADMIN_ROLES", create_type=True)
forum_admins = db.Table(
    "forum_admins",
    db.Column("forum_id", INTEGER, db.ForeignKey("forums.id")),
    db.Column("user_id", BIGINT, db.ForeignKey("users.id")),
    db.Column("role", ADMIN_ROLES, nullable = False, server_default = text(f"'{ADMIN_ROLES.enums[0]}'")),         # Awful hack alert
    db.PrimaryKeyConstraint("forum_id", "user_id", name = "pk_forum_admins")
)

### Tables ###
class User(db.Model):
    __tablename__ = "users"

    ### Attributes ###
    # Basic identification
    id : int = db.Column(BIGINT, nullable = False, autoincrement=True)
    username : str = db.Column(VARCHAR(64), nullable = False, unique=True, index=True)
    _alias : str = db.Column(VARCHAR(64), nullable = True)
    email : str = db.Column(VARCHAR(320), nullable = False, unique=True, index=True)
    rtfb : bool = db.Column(BOOLEAN, server_default=text('false'), nullable = False)

    pfp : str = db.Column(VARCHAR(256))

    # Passwords and salts
    pw_hash : bytes = db.Column(BYTEA(256), nullable = False)
    pw_salt : bytes = db.Column(BYTEA(64), nullable = False)

    # Activity
    aura : int = db.Column(BIGINT, default = 0)
    total_posts : int = db.Column(INTEGER, default = 0)
    total_comments : int = db.Column(INTEGER, default = 0)
    time_joined : datetime = db.Column(TIMESTAMP, nullable = False, server_default=text("CURRENT_TIMESTAMP"))
    last_login : datetime = db.Column(TIMESTAMP)
    deleted : bool= db.Column(BOOLEAN, nullable=False, server_default=text("false"))
    time_deleted : datetime = db.Column(TIMESTAMP, nullable=True)

    ### Relationships ###
    posts : Mapped[list["Post"]] = db.relationship("Post", back_populates="authored_by", uselist=True, lazy="select")
    comments : Mapped[list["Comment"]] = db.relationship("Comment", back_populates="author_id", lazy="select")
    tickets : Mapped[list["UserTicket"]] = db.relationship("UserTicket", back_populates="parent_user", lazy="select")
    password_token : Mapped[list["PasswordRecoveryToken"]] = db.relationship("PasswordRecoveryToken", back_populates="parent_user", lazy="select")
    #NOTE:  Only query the related attributes when necessary (attribute access time, typically GET /users/<user_id>), not on any other queries where a user might be part of the SELECT query, such as author (posts, comments, forum rules) or in GET /users/search?q=some-string

    __table_args__ = (
        PrimaryKeyConstraint("id"),
        CheckConstraint("LENGTH(username) > 5", name="ck_users_username_length"),
        CheckConstraint("_alias IS NULL OR LENGTH(_alias) > 5", name="ck_users_alias_length"),
        CheckConstraint(r"email ~*'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'", name="ck_users_email_regex"),
    )

    def __repr__(self) -> str:
        return f"<User({self.id}, {self.username}, {self.aura}, {self._alias}, {self.email}, {self.total_posts}, {self.total_comments}, {self.date_joined.strftime('%d/%m/%y, %H:%M:%S')}, {self.last_login.strftime('%d/%m/%y, %H:%M:%S')})>"
    
    def __json_like__(self) -> dict:
        return {"id" : self.id,
                "username" : self.username,
                "alias" : self._alias,
                "aura" : self.aura,
                "posts" : self.total_posts,
                "comments" : self.total_comments,
                "epoch" : self.time_joined.strftime('%d/%m/%y, %H:%M:%S'),
                "last_login" : self.last_login.strftime('%d/%m/%y, %H:%M:%S')}

@dataclass
class UserTicket(db.Model):
    __tablename__ = 'user_tickets'

    user_id : int = db.Column(BIGINT, db.ForeignKey("users.id"))
    time_raised : datetime = db.Column(TIMESTAMP, nullable = False, server_default = text("CURRENT_TIMESTAMP"))
    description : str = db.Column(VARCHAR(512), nullable = False)

    parent_user : Mapped[User]= db.relationship("User", back_populates="tickets", lazy="select")

    __table_args__ = (
        PrimaryKeyConstraint("user_id"),
    )

@dataclass
class PasswordRecoveryToken(db.Model):
    __tablename__ = "password_recovery_tokens"

    user_id : int = db.Column(BIGINT, db.ForeignKey("users.id"))
    expiry : datetime = db.Column(TIMESTAMP, nullable = False, server_default = text("CURRENT_TIMESTAMP"), index=True)
    url_hash : str = db.Column(BYTEA(512), nullable = False, unique = True, index = True)

    parent_user : Mapped[User]= db.relationship("User", back_populates="password_token", lazy="select")

    __table_args__ = (
        PrimaryKeyConstraint("user_id"),
    )

@dataclass
class Anime(db.Model):
    __tablename__ = "animes"

    id : int = db.Column(INTEGER, nullable = False, autoincrement = True)
    title : str = db.Column(VARCHAR(128), nullable = False, index = True, unique = True)

    rating : float = db.Column(NUMERIC(3,2), nullable = False)
    mal_ranking : int = db.Column(INTEGER, nullable = True)
    members : int = db.Column(BIGINT, nullable = False, server_default = text("0"))
    synopsis : str = db.Column(TEXT, nullable = False)
    # Genres is multi-valued, made into separate table
    # Stream links are multi-valued, made into separate table

    registered_forums : Mapped[list["Forum"]] = db.relationship("Forum", lazy = "select")

    def __json_like__(self) -> dict:
        return {"id" : self.id,
                "title" : self.title,
                "rating" : self.rating,
                "mal_ranking" : self.mal_ranking,
                "members" : self.members,
                "synopsis" : self.synposis[:16] + "..."}
    #TODO: Add stream links and genres to be queried in either __json_like__ or some other Python-level method

    __table_args__ = (
        PrimaryKeyConstraint("id"),
        CheckConstraint("mal_ranking >= 0", "check_ranking_positive"),
        CheckConstraint("members >= 0", "check_members_positive"),
        CheckConstraint("rating >= 0", "check_rating_positive"),
    )

@dataclass
class Genre(db.Model):
    __tablename__ = "genres"
    id = db.Column(SMALLINT, autoincrement = True)
    _name = db.Column(VARCHAR(16), nullable = False, unique = True, index = True)

    __table_args__ = (
        PrimaryKeyConstraint("id"),
    )

class Forum(db.Model):
    __tablename__ = "forums"

    # Basic identification
    id : int = db.Column(INTEGER, nullable = False, autoincrement = True)
    _name : str = db.Column(VARCHAR(64), nullable = False, unique=True, index=True)
    anime : int | None = db.Column(INTEGER, db.ForeignKey("animes.id"), index = True)
 
    # Appearance
    color_theme : int = db.Column(SMALLINT, nullable = False, server_default = "1")
    pfp : str = db.Column(VARCHAR(128))
    description : str = db.Column(VARCHAR(256))

    # Activity stats
    subscribers : int = db.Column(BIGINT, nullable = False, default = 0)
    posts : int = db.Column(BIGINT, nullable = False, default = 0)
    highlight_post_1 : int = db.Column(BIGINT, nullable = True)
    highlight_post_2 : int = db.Column(BIGINT, nullable = True)
    highlight_post_3 : int = db.Column(BIGINT, nullable = True)

    created_at : datetime = db.Column(TIMESTAMP, nullable = False)
    admin_count : int = db.Column(SMALLINT, default = 1)

    ### Relationships ###
    rules : Mapped[list["ForumRules"]] = db.relationship("ForumRules", back_populates="forum", uselist=True, lazy="select")       # 1:M
    #NOTE: Relationship (M:1) between posts and forums is ommitted at the SQLAlchemy level, because of separate logic at the same level (LIMIT*OFFSET+ORDER BY)

    __table_args__ = (
        PrimaryKeyConstraint("id"),
        CheckConstraint("posts >= 0", name="check_posts_value"),
        CheckConstraint("subscribers >= 0", name="check_subs_values"),
        CheckConstraint("color_theme > 0 AND color_theme < 20", name="limit_color_themes"),
        CheckConstraint("admin_count > 0", name="check_atleast_1_admin"),
        UniqueConstraint("_name", "anime", name="uq_name_anime"),
    )
    def __repr__(self) -> str:
        return f"<Forum({self.id}, {self._name}, {self.color_theme}, {self.pfp}, {self.description}, {self.subscribers}, {self.posts}, {self.highlight_post_1}, {self.highlight_post_2}, {self.highlight_post_3}, {self.created_at.strftime('%d/%m/%y, %H:%M:%S'), {self.admin_count}})>"
    
    def __json_like__(self) -> str:
        return {"id" : self.id,
                "name" : self.name,
                "pfp" : self.pfp,
                "color_theme" : self.color_theme,
                "description" : self.description,
                "posts" : self.posts,
                "highlights" : [self.highlight_post_1, self.highlight_post_2, self.highlight_post_3],
                "epoch" : self.created_at.strftime('%d/%m/%y, %H:%M:%S'), 
                "admin_count" : self.admin_count}

class ForumRules(db.Model):
    __tablename__ = "forum_rules"

    #Identification
    forum_id : int = db.Column(INTEGER, db.ForeignKey("forums.id"), nullable = False)
    
    # Data
    rule_number : int = db.Column(SMALLINT, nullable = False, unique=True, autoincrement=True)
    title : str = db.Column(VARCHAR(32), nullable = False)
    body : str = db.Column(VARCHAR(128), server_default="No additional description provided for this rule.")
    author : int = db.Column(INTEGER, nullable = False)

    time_created : datetime = db.Column(TIMESTAMP, nullable = False)

    ### Relationships ###
    forum : Mapped["Forum"] = db.relationship("Forum", back_populates="rules", lazy="select")      # M:1

    __table_args__ = (
        PrimaryKeyConstraint("forum_id", "rule_number", name="pk_forum_rules"),
        CheckConstraint("rule_number BETWEEN 0 AND 5", "enforce_forum_rules_range"),
    )

    def __repr__(self) -> str:
        return f"<Forum_Rules(f{self.forum_id}, {self.rule_number}, {self.title if len(self.title) < 16 else self.title[:16]+'...'}, {self.body if len(self.body) < 16 else self.body[:16]+'...'}, {self.author}, {self.time_created.strftime('%d/%m/%y, %H:%M:%S')})>"
    
    def __json_like__(self) -> dict:
        return {"forum_id" : self.forum_id,
                "rule_number" : self.rule_number,
                "title" : self.title,
                "body" : self.body,
                "author" : self.author,
                "epoch" : self.time_created.strftime('%d/%m/%y, %H:%M:%S')}

class Post(db.Model):
    __tablename__ = "posts"

    ### Attributes ###
    # Basic identification
    id : int = db.Column(BIGINT, nullable = False, autoincrement = True)
    author_id : int = db.Column(BIGINT, db.ForeignKey("users.id"), nullable = False, index=True)
    forum : str = db.Column(VARCHAR(128), nullable = False)

    # Post statistics
    score : int = db.Column(INTEGER, default = 0)
    total_comments : int = db.Column(INTEGER, default = 0)

    # Post details
    title : str = db.Column(VARCHAR(64), nullable = False, index=True)
    body_text : str = db.Column(TEXT, nullable = False)
    flair : str = db.Column(VARCHAR(16), index=True)
    closed : bool = db.Column(BOOLEAN, default=False)
    time_posted : datetime = db.Column(TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    saves : int = db.Column(INTEGER, default=0)
    reports : int = db.Column(INTEGER, default=0)

    ### Relationships ###
    authored_by : Mapped["User"] = db.relationship("User", back_populates="posts", lazy="select")        # M:1
    has_comments : Mapped[list["Comment"]] = db.relationship("Comment", back_populates="post", lazy="select")    # 1:M
    parent_forum : Mapped["Post"] = db.relationship("Parent", back_populates="child_posts")             # M:1


    __table_args__ = (
        PrimaryKeyConstraint("id"),
        CheckConstraint("LENGTH(title) > 8", name="check_title_length_over_8"),
    )

    def __repr__(self) -> str:
        return f"<Post({self.id}, {self.author_id}, {self.author_uname}, {self.forum}, {self.score}, {self.total_comments}, {self.title if len(self.title) < 16 else self.title[:16] + '...'}, {self.body_text if len(self.body_text) < 32 else self.body_text[:32]+'...'}, {self.flair}, {self.closed}, {self.time_posted.strftime('%d/%m/%y, %H:%M:%S')}, {self.saves}, {self.reports})>"
    
    def __json_like__(self) -> dict:
        return {"id" : self.id,
                "author_id" : self.author_id,
                "author_username" : self.author_uname,
                "forum" : self.forum,
                "score" : self.score,
                "comments" : self.total_comments,
                "title" : self.title,
                "body" : self.body_text,
                "flair" : self.flair,
                "closed" : self.closed,
                "epoch" : self.time_posted.strftime("%d/%m/%y, %H:%M:%S"),
                "saves" : self.saves}

class Comment(db.Model):
    __tablename__ = "comments"

    ### Attributes ###
    # Basic identification
    id : int = db.Column(BIGINT, nullable=False, autoincrement = True)
    author_id : int = db.Column(BIGINT, db.ForeignKey("users.id"), nullable = False, index=True)
    parent_forum : int = db.Column(INTEGER, nullable = False)

    # Comment details
    time_created : datetime = db.Column(TIMESTAMP, nullable = False, server_default=text("CURRENT_TIMESTAMP"))
    body : str = db.Column(VARCHAR(512), nullable=False)
    parent_post : int = db.Column(BIGINT, db.ForeignKey("posts.id"), nullable=False, index=True)
    parent_thread : int = db.Column(BIGINT, db.ForeignKey("comments.id"))
    replying_to : int = db.Column(BIGINT, db.ForeignKey("comments.id"))
    score : int = db.Column(INTEGER, default = 0)
    reports : int= db.Column(INTEGER, default = 0)

    ### Relationships ###
    author_id : Mapped["User"] = db.relationship("User", back_populates="comments")   # M:1
    post : Mapped["Post"] = db.relationship("Post", back_populates="has_comments")    # M:1
    parent : Mapped["Comment"] = db.relationship("Comment", back_populates="parent")   # 1:1, Unary
    child : Mapped["Comment"] = db.relationship("Comment", back_populates="child")   # 1:1, Unary
    parent_comment : Mapped["Comment"] = db.relationship("Comment", back_populates="comment_replied")  # 1:1, Unary
    comment_replied : Mapped["Comment"] = db.relationship("Comment", back_populates="parent_comment")  # 1:1, Unary

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_comments"),
        CheckConstraint("reports >= 0", "check_reports_value"),
    )

    def __repr__(self) -> str:
        return f"<Comment({self.id}, {self.author_id}, {self.parent_forum}, {self.time_created.strftime('%d/%m/%y, %H:%M:%S')}, {self.body}, {self.parent_post}, {self.parent_thread}, {self.replying_to}, {self.score}, {self.reports}, {self.author_id}, {self.post}, {self.parent}, {self.child}, {self.parent_comment}, {self.comment_replied})>"
    
    def __json_like__(self) -> dict:
        return {"id" : self.id,
                "author_id" : self.author_id,
                "parent_forum" : self.parent_forum,
                "time_created" : self.time_created.strftime('%d/%m/%y, %H:%M:%S'),
                "body" : self.body,
                "parent_post" : self.parent_post,
                "parent_thread" : self.parent_thread,
                "replying_to" : self.replying_to,
                "score" : self.score}