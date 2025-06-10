from flask_sqlalchemy import SQLAlchemy

from sqlalchemy import PrimaryKeyConstraint, CheckConstraint, UniqueConstraint, MetaData
from sqlalchemy.sql import text
from sqlalchemy.dialects.postgresql import TIMESTAMP, BYTEA, ENUM
from sqlalchemy.types import INTEGER, SMALLINT, BOOLEAN, VARCHAR, BIGINT, NUMERIC, TEXT

import orjson, os
from enum import Enum
from datetime import datetime

from dataclasses import dataclass

CONFIG: dict = {}
with open(os.path.join(os.path.dirname(__file__), "instance", "config.json"), 'rb') as configFile:
    CONFIG = orjson.loads(configFile.read())
    METADATA = MetaData(naming_convention=CONFIG["database"]["naming_convention"])

db = SQLAlchemy()

### Assosciation Tables ###
class ForumSubscription(db.Model):
    __tablename__ = "forum_subscriptions"
    user_id: int = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete='CASCADE'), primary_key=True)
    forum_id: int = db.Column(db.Integer, db.ForeignKey("forums.id", ondelete='CASCADE'), primary_key=True)
    time_subscribed: datetime = db.Column(db.TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"))

class AnimeSubscription(db.Model):
    __tablename__ = "anime_subscriptions"
    user_id: int = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete='CASCADE'), primary_key=True)
    anime_id: int = db.Column(db.Integer, db.ForeignKey("animes.id", ondelete='CASCADE'), primary_key=True)

class PostVote(db.Model):
    __tablename__ = "post_votes"
    voter_id: int = db.Column(db.BigInteger, db.ForeignKey("users.id"), primary_key=True)
    post_id: int = db.Column(db.BigInteger, db.ForeignKey("posts.id", ondelete='CASCADE'), primary_key=True)
    vote: bool = db.Column(db.Boolean, nullable=False)

class PostSave(db.Model):
    __tablename__ = "post_saves"
    user_id: int = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete='CASCADE'), primary_key=True)
    post_id: int = db.Column(db.BigInteger, db.ForeignKey("posts.id", ondelete='CASCADE'), primary_key=True)

REPORT_TAGS = ENUM('spam', 'harassment', 'hate', 'violence', 'other', name='REPORT_TAGS', create_type=True)
class ReportTags(Enum):
    spam = 'spam'
    harassment = 'harassment'
    hate = 'hate'
    violence = 'violence'
    other = 'other'

    @staticmethod
    def check_membership(arg: str) -> bool:
        return arg in [v.value for v in ReportTags.__members__.values()]

class PostReport(db.Model):
    __tablename__ = "post_reports"
    user_id: int = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete='CASCADE'), primary_key=True)
    post_id: int = db.Column(db.BigInteger, db.ForeignKey("posts.id", ondelete='CASCADE'), primary_key=True)
    report_tag: str = db.Column(REPORT_TAGS, nullable=False, primary_key=True)
    report_time: datetime = db.Column(TIMESTAMP, default = text('CURRENT_TIMESTAMP'))
    report_description: str = db.Column(VARCHAR(256), nullable = False)

class StreamLink(db.Model):
    __tablename__ = "stream_links"
    anime_id: int = db.Column(db.Integer, db.ForeignKey("animes.id", ondelete='CASCADE'), primary_key=True)
    url: str = db.Column(db.String(256), primary_key=True, nullable=False)
    website: str = db.Column(db.String(128), nullable=False)

class AnimeGenre(db.Model):
    __tablename__ = "anime_genres"
    anime_id: int = db.Column(db.Integer, db.ForeignKey("animes.id", ondelete='CASCADE'), primary_key=True)
    genre_id: int = db.Column(db.SmallInteger, db.ForeignKey("genres.id", ondelete='CASCADE'), primary_key=True)

ADMIN_ROLES = ENUM("admin", "super", "owner", name="ADMIN_ROLES", create_type=True)
class AdminRoles(Enum):
    '''Python representation of ADMIN_ROLES Enum in Postgres'''
    admin = 1
    super = 2
    owner = 3

    @staticmethod
    def check_membership(arg: str) -> bool:
        return arg in [v.value for v in AdminRoles.__members__.values()]
    
    @staticmethod
    def getAdminAccessLevel(role: str) -> int:
        '''Get corresponding access level of admin role, return -1 on failure'''
        try:
            return AdminRoles[role].value
        except:
            return -1

class ForumAdmin(db.Model):
    __tablename__ = "forum_admins"
    forum_id: int = db.Column(db.Integer, db.ForeignKey("forums.id", ondelete='CASCADE'), primary_key=True)
    user_id: int = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete='CASCADE'), primary_key=True)
    role: str = db.Column(ADMIN_ROLES, nullable=False, server_default=text(f"'{ADMIN_ROLES.enums[0]}'"))


### Tables ###
db.Model.__attrdict__ = lambda x : {k : v for k,v in x.__dict__.items() if k != '_sa_instance_state'}

class User(db.Model):
    __tablename__ = "users"

    ### Attributes ###
    # Basic identification
    id: int = db.Column(BIGINT, nullable = False, autoincrement=True)
    username: str = db.Column(VARCHAR(64), nullable = False, unique=True, index=True)
    email: str = db.Column(VARCHAR(320), nullable = False, unique=True, index=True)
    rtbf: bool = db.Column(BOOLEAN, server_default=text('false'), nullable = False)

    pfp: str = db.Column(VARCHAR(256))

    # Passwords and salts
    pw_hash: bytes = db.Column(BYTEA(256), nullable = False)
    pw_salt: bytes = db.Column(BYTEA(64), nullable = False)

    # Activity
    aura: int = db.Column(BIGINT, default = 0)
    total_posts: int = db.Column(INTEGER, default = 0)
    total_comments: int = db.Column(INTEGER, default = 0)
    time_joined: datetime = db.Column(TIMESTAMP, nullable = False, server_default=text("CURRENT_TIMESTAMP"))
    last_login: datetime = db.Column(TIMESTAMP)

    deleted: bool= db.Column(BOOLEAN, nullable=False, server_default=text("false"))
    time_deleted: datetime = db.Column(TIMESTAMP, nullable=True)
    
    __table_args__ = (
        PrimaryKeyConstraint("id"),
        CheckConstraint("LENGTH(username) > 5", name="ck_users_username_length"),
        CheckConstraint("_alias IS NULL OR LENGTH(_alias) > 5", name="ck_users_alias_length"),
        CheckConstraint(r"email ~*'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'", name="ck_users_email_regex"),
    )

    def __repr__(self) -> str:
        return f"<User({self.id}, {self.username}, {self.aura}, {self._alias}, {self.email}, {self.total_posts}, {self.total_comments}, {self.date_joined.strftime('%d/%m/%y, %H:%M:%S')}, {self.last_login.strftime('%d/%m/%y, %H:%M:%S')})>"
    
    def __json_like__(self) -> dict[str, str|int]:
        return {"id": self.id,
                "username": self.username,
                "aura": self.aura,
                "posts": self.total_posts,
                "comments": self.total_comments,
                "epoch": self.time_joined.strftime('%d/%m/%y, %H:%M:%S'),
                "last_login": self.last_login.strftime('%d/%m/%y, %H:%M:%S')}

@dataclass
class UserTicket(db.Model):
    __tablename__ = 'user_tickets'

    id: int = db.Column(BIGINT)
    user_id: int = db.Column(BIGINT, db.ForeignKey("users.id"))
    email: str = db.Column(VARCHAR(320), nullable=False, index=True)
    time_raised: datetime = db.Column(TIMESTAMP, nullable = False, server_default = text("CURRENT_TIMESTAMP"))
    description: str = db.Column(VARCHAR(512), nullable = False)

    __table_args__ = (
        PrimaryKeyConstraint("id"),
    )

@dataclass
class PasswordRecoveryToken(db.Model):
    __tablename__ = "password_recovery_tokens"

    user_id: int = db.Column(BIGINT, db.ForeignKey("users.id"))
    expiry: datetime = db.Column(TIMESTAMP, nullable = False, server_default = text("CURRENT_TIMESTAMP"), index=True)
    url_hash: bytes = db.Column(BYTEA(512), nullable = False, unique = True, index = True)

    __table_args__ = (
        PrimaryKeyConstraint("user_id"),
    )

@dataclass
class Anime(db.Model):
    __tablename__ = "animes"

    id: int = db.Column(INTEGER, nullable = False, autoincrement = True)
    title: str = db.Column(VARCHAR(128), nullable = False, index = True, unique = True)

    rating: float = db.Column(NUMERIC(3,2), nullable = False)
    mal_ranking: int = db.Column(INTEGER, nullable = True)
    members: int = db.Column(BIGINT, nullable = False, server_default = text("0"))
    synopsis: str = db.Column(TEXT, nullable = False)
    banner: str = db.Column(VARCHAR(128), nullable=True)
    # Genres is multi-valued, made into separate table
    # Stream links are multi-valued, made into separate table


    def __json_like__(self) -> dict[str, int]:
        return {"id": self.id,
                "title": self.title,
                "rating": float(self.rating),
                "banner" : self.banner, 
                "mal_ranking": self.mal_ranking,
                "members": self.members,
                "synopsis": self.synopsis}
    
    __table_args__ = (
        PrimaryKeyConstraint("id"),
        CheckConstraint("mal_ranking >= 0", "check_ranking_positive"),
        CheckConstraint("members >= 0", "check_members_positive"),
        CheckConstraint("rating >= 0", "check_rating_positive"),
    )

@dataclass
class Genre(db.Model):
    __tablename__ = "genres"
    id: int = db.Column(SMALLINT, autoincrement = True)
    _name: str = db.Column(VARCHAR(16), nullable = False, unique = True, index = True)

    __table_args__ = (
        PrimaryKeyConstraint("id"),
    )

class Forum(db.Model):
    __tablename__ = "forums"

    # Basic identification
    id: int = db.Column(INTEGER, nullable = False, autoincrement = True)
    _name: str = db.Column(VARCHAR(64), nullable = False, unique=True, index=True)
    anime: int = db.Column(INTEGER, db.ForeignKey("animes.id"), index = True, nullable=True)
 
    # Appearance
    description: str = db.Column(VARCHAR(256), nullable = True)

    # Activity stats
    subscribers: int = db.Column(BIGINT, nullable = False, default = 0, server_default=text('0'))
    posts: int = db.Column(BIGINT, nullable = False, default = 0, server_default=text('0'))
    highlight_post_1: int = db.Column(BIGINT, nullable = True)
    highlight_post_2: int = db.Column(BIGINT, nullable = True)
    highlight_post_3: int = db.Column(BIGINT, nullable = True)

    created_at: datetime = db.Column(TIMESTAMP, nullable = False)
    admin_count: int = db.Column(SMALLINT, default = 1, server_default=text('1'), nullable=False)

    # Deletion metadata
    deleted: bool = db.Column(BOOLEAN, nullable=False, server_default=text("false"))
    time_deleted: datetime = db.Column(TIMESTAMP, nullable=True)
    rtbf_hidden: bool = db.Column(BOOLEAN, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("id"),
        CheckConstraint("posts >= 0", name="check_posts_value"),
        CheckConstraint("subscribers >= 0", name="check_subs_values"),
        CheckConstraint("color_theme > 0 AND color_theme < 20", name="limit_color_themes"),
        CheckConstraint("admin_count > 0", name="check_atleast_1_admin"),
        UniqueConstraint("_name", "anime", name="uq_name_anime"),
    )
    
    def __init__(self, name: str, anime: int, desc: str, epoch: datetime | None = None) -> None:
        self._name = name
        self.anime = anime
        self.description = desc
        self.subscribers = 0
        self.posts = 0
        self.highlight_post_1 = None; self.highlight_post_2 = None; self.highlight_post_3 = None
        self.created_at = epoch or datetime.now()
        self.admin_count = 1
        self.deleted = False; self.time_deleted = None
    
    def __repr__(self) -> str:
        return f"<Forum({self.id}, {self._name}, {self.description}, {self.subscribers}, {self.posts}, {self.highlight_post_1}, {self.highlight_post_2}, {self.highlight_post_3}, {self.created_at.strftime('%d/%m/%y, %H:%M:%S'), {self.admin_count}})>"
    
    def __json_like__(self) -> dict[str, str|int]:
        return {"id": self.id,
                "name": self._name,
                "subscribers" : self.subscribers,
                "description": self.description,
                "posts": self.posts,
                "highlight_1": self.highlight_post_1,
                "highlight_2": self.highlight_post_2,
                "highlight_3": self.highlight_post_3,
                "epoch": self.created_at.strftime('%d/%m/%y, %H:%M:%S'), 
                "admin_count": self.admin_count}

class ForumRules(db.Model):
    __tablename__ = "forum_rules"

    #Identification
    forum_id: int = db.Column(INTEGER, db.ForeignKey("forums.id", ondelete='CASCADE'), nullable = False)
    
    # Data
    rule_number: int = db.Column(SMALLINT, nullable = False, unique=True, autoincrement=True)
    title: str = db.Column(VARCHAR(32), nullable = False)
    body: str = db.Column(VARCHAR(128), server_default="No additional description provided for this rule.")
    author: int = db.Column(INTEGER, nullable = False)

    time_created: datetime = db.Column(TIMESTAMP, nullable = False)

    __table_args__ = (
        PrimaryKeyConstraint("forum_id", "rule_number", name="pk_forum_rules"),
        CheckConstraint("rule_number BETWEEN 0 AND 5", "enforce_forum_rules_range"),
    )

    def __init__(self, forumID: int, ruleNumber: int, title: str, body: str, authorID: int, time_created: datetime | None = None) -> None:
        self.forum_id = forumID
        self.rule_number = ruleNumber
        self.title = title
        self.body = body
        self.author = authorID
        self.time_created = time_created or datetime.now()

    def __repr__(self) -> str:
        return f"<Forum_Rules(f{self.forum_id}, {self.rule_number}, {self.title if len(self.title) < 16 else self.title[:16]+'...'}, {self.body if len(self.body) < 16 else self.body[:16]+'...'}, {self.author}, {self.time_created.strftime('%d/%m/%y, %H:%M:%S')})>"
    
    def __json_like__(self) -> dict:
        return {"forum_id": self.forum_id,
                "rule_number": self.rule_number,
                "title": self.title,
                "body": self.body,
                "author": self.author,
                "epoch": self.time_created.strftime('%d/%m/%y, %H:%M:%S')}

class Post(db.Model):
    __tablename__ = "posts"

    ### Attributes ###
    # Basic identification
    id: int = db.Column(BIGINT, nullable = False, autoincrement = True)
    author_id: int = db.Column(BIGINT, db.ForeignKey("users.id"), nullable = False, index=True)
    forum_id: int = db.Column(INTEGER, db.ForeignKey("forums.id", ondelete='CASCADE'), nullable = False)

    # Post statistics
    score: int = db.Column(INTEGER, default = 0, server_default=text('0'), nullable=False)
    total_comments: int = db.Column(INTEGER, default = 0, server_default=text('0'), nullable=False)

    # Post details
    title: str = db.Column(VARCHAR(64), nullable = False, index=True)
    body_text: str = db.Column(TEXT, nullable = False)
    flair: str = db.Column(VARCHAR(16), index=True)
    closed: bool = db.Column(BOOLEAN, default=False)
    time_posted: datetime = db.Column(TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    saves: int = db.Column(INTEGER, default=0, server_default=text('0'), nullable=False)
    reports: int = db.Column(INTEGER, default=0, server_default=text('0'), nullable=False)

    # Deletion metadata
    deleted: bool= db.Column(BOOLEAN, nullable=False, server_default=text("false"))
    time_deleted: datetime = db.Column(TIMESTAMP, nullable=True)
    rtbf_hidden: bool = db.Column(BOOLEAN, nullable=True)

    def __init__(self, author_id: int, forum_id: int, title: str, body_text: str, epoch: datetime, flair: str = None, score: int = 0, total_comments: int = 0, closed: bool = False):
        self.author_id = author_id
        self.forum_id = forum_id
        self.score = score
        self.total_comments = total_comments
        self.title = title
        self.body_text = body_text
        self.flair = flair
        self.closed = closed
        self.time_posted = epoch
        self.saves = 0
        self.reports = 0
        self.deleted = False
        self.time_deleted = None


    __table_args__ = (
        PrimaryKeyConstraint("id"),
        CheckConstraint("LENGTH(title) > 8", name="check_title_length_over_8"),
    )

    def __repr__(self) -> str:
        return f"<Post({self.id}, {self.author_id}, {self.forum_id}, {self.score}, {self.total_comments}, {self.title if len(self.title) < 16 else self.title[:16] + '...'}, {self.body_text if len(self.body_text) < 32 else self.body_text[:32]+'...'}, {self.flair}, {self.closed}, {self.time_posted.strftime('%d/%m/%y, %H:%M:%S')}, {self.saves}, {self.reports})>"
    
    def __json_like__(self) -> dict[str, str|int]:
        return {"id": self.id,
                "author_id": self.author_id,
                "forum": self.forum_id,
                "score": self.score,
                "comments": self.total_comments,
                "title": self.title,
                "body_text": self.body_text,
                "flair": self.flair,
                "closed": self.closed,
                "epoch": self.time_posted.strftime("%d/%m/%y, %H:%M:%S"),
                "saves": self.saves}

class Comment(db.Model):
    __tablename__ = "comments"

    ### Attributes ###
    # Basic identification
    id: int = db.Column(BIGINT, nullable=False, autoincrement = True)
    author_id: int = db.Column(BIGINT, db.ForeignKey("users.id"), nullable = False, index=True)
    parent_forum: int = db.Column(INTEGER, db.ForeignKey("forums.id", ondelete='CASCADE'), nullable = False)

    # Comment details
    time_created: datetime = db.Column(TIMESTAMP, nullable = False, server_default=text("CURRENT_TIMESTAMP"))
    body: str = db.Column(VARCHAR(512), nullable=False)
    parent_post: int = db.Column(BIGINT, db.ForeignKey("posts.id", ondelete='CASCADE'), nullable=False, index=True)
    reports: int= db.Column(INTEGER, default = 0, server_default=text('0'), nullable=False)

    # Deletion metadata
    deleted : bool= db.Column(BOOLEAN, nullable=False, server_default=text("false"))
    time_deleted : datetime = db.Column(TIMESTAMP, nullable=True)
    rtbf_hidden: bool = db.Column(BOOLEAN, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_comments"),
        CheckConstraint("reports >= 0", "check_reports_value"),
    )

    def __init__(self, authorID: int, parentForum: int, epoch: datetime, body: str, parentPost: int):
        self.author_id = authorID
        self.parent_forum = parentForum
        self.time_created = epoch
        self.body = body
        self.parent_post = parentPost
        self.score = 0
        self.reports = 0
        self.deleted = False
        self.time_deleted = None

    def __repr__(self) -> str:
        return f"<Comment({self.id}, {self.author_id}, {self.parent_forum}, {self.time_created.strftime('%d/%m/%y, %H:%M:%S')}, {self.body}, {self.parent_post}, {self.reports})>"
    
    def __json_like__(self) -> dict[str, str|int]:
        return {"id": self.id,
                "author_id": self.author_id,
                "parent_forum": self.parent_forum,
                "time_created": self.time_created.strftime('%d/%m/%y, %H:%M:%S'),
                "body": self.body,
                "parent_post": self.parent_post}
