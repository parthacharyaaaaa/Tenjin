from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

from sqlalchemy import PrimaryKeyConstraint, CheckConstraint, MetaData
from sqlalchemy.sql import text
from sqlalchemy.orm import Mapped
from sqlalchemy.dialects.postgresql import TIMESTAMP, BYTEA
from sqlalchemy.types import INTEGER, SMALLINT, BOOLEAN, VARCHAR, BIGINT, TEXT

import orjson, os

CONFIG : dict = {}
with open(os.path.join(os.path.dirname(__file__), "instance", "config.json"), 'rb') as configFile:
    CONFIG = orjson.loads(configFile.read())
    METADATA = MetaData(naming_convention=CONFIG["database"]["naming_convention"])

db = SQLAlchemy()
migrate = Migrate(db=db)

### Assosciation Tables ###
user_subscriptions = db.Table(
    "user_subscriptions",
    db.Column("user_id", BIGINT, db.ForeignKey("users.id")),
    db.Column("forum_id", INTEGER, db.ForeignKey("forums.id")),
    db.Column("time_subscribed", TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    db.PrimaryKeyConstraint("user_id", "forum_id", name="pk_forum_subscriptions")
)

comment_votes = db.Table(
    "comment_votes",
    db.Column("voter_id", BIGINT, db.ForeignKey("users.id")),
    db.Column("comment_id", BIGINT, db.ForeignKey("comments.id")),
    db.Column("vote", BOOLEAN, nullable=False),
    db.PrimaryKeyConstraint("voter_id", "comment_id", name="pk_comment_votes")
)

post_votes = db.Table(
    "post_votes",
    db.Column("voter_id", BIGINT, db.ForeignKey("users.id")),
    db.Column("post_id", BIGINT, db.ForeignKey("posts.id")),
    db.Column("vote", BOOLEAN, nullable=False),
    db.PrimaryKeyConstraint("voter_id", "post_id", name="pk_post_votes")
)

### Tables ###
class User(db.Model):
    __tablename__ = "users"

    ### Attributes ###
    # Basic identification
    id = db.Column(BIGINT, nullable = False, autoincrement=True)
    username = db.Column(VARCHAR(64), nullable = False, unique=True, index=True)
    _alias = db.Column(VARCHAR(64), nullable = True)
    email = db.Column(VARCHAR(320), nullable = False, unique=True, index=True)

    pfp = db.Column(VARCHAR(256))

    # Passwords and salts
    pw_hash = db.Column(BYTEA(256), nullable = False)
    pw_salt = db.Column(BYTEA(64), nullable = False)

    # Activity
    aura = db.Column(BIGINT, default = 0)
    total_posts = db.Column(INTEGER, default = 0)
    total_comments = db.Column(INTEGER, default = 0)
    time_joined = db.Column(TIMESTAMP, nullable = False, server_default=text("CURRENT_TIMESTAMP"))
    last_login = db.Column(TIMESTAMP)
    deleted = db.Column(BOOLEAN, nullable=False, server_default=text("false"))
    time_deleted = db.Column(TIMESTAMP, nullable=True)
    
    ### Relationships ###
    posts : Mapped[list["Post"]] = db.relationship("Post", back_populates="authored_by", uselist=True, lazy="select")
    comments : Mapped[list["Comment"]] = db.relationship("Comment", back_populates="author_id", lazy="select")
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

class Forum(db.Model):
    __tablename__ = "forums"

    # Basic identification
    id = db.Column(INTEGER, nullable = False, autoincrement = True)
    _name = db.Column(VARCHAR(64), nullable = False, unique=True, index=True)
 
    # Appearance
    color_theme = db.Column(SMALLINT, nullable = False, server_default = "1")
    pfp = db.Column(VARCHAR(128))
    description = db.Column(VARCHAR(256))

    # Activity stats
    subscribers = db.Column(BIGINT, nullable = False, default = 0)
    posts = db.Column(BIGINT, nullable = False, default = 0)
    highlight_post_1 = db.Column(BIGINT, nullable = True)
    highlight_post_2 = db.Column(BIGINT, nullable = True)
    highlight_post_3 = db.Column(BIGINT, nullable = True)

    created_at = db.Column(TIMESTAMP, nullable = False)
    admin_count = db.Column(SMALLINT, default = 1)

    ### Relationships ###
    rules : Mapped[list["Forum_Rules"]]= db.relationship("Forum_Rules", back_populates="forum", uselist=True, lazy="select")       # 1:M
    #NOTE: Relationship (M:1) between posts and forums is ommitted at the SQLAlchemy level, because of separate logic at the same level (LIMIT*OFFSET+ORDER BY)

    __table_args__ = (
        PrimaryKeyConstraint("id"),
        CheckConstraint("posts >= 0", name="check_posts_value"),
        CheckConstraint("subscribers >= 0", name="check_subs_values"),
        CheckConstraint("color_theme > 0 AND color_theme < 20", name="limit_color_themes"),
        CheckConstraint("admin_count > 0", name="check_atleast_1_admin"),
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

class Forum_Rules(db.Model):
    __tablename__ = "forum_rules"

    #Identification
    forum_id = db.Column(INTEGER, db.ForeignKey("forums.id"), nullable = False)
    
    # Data
    rule_number = db.Column(SMALLINT, nullable = False, unique=True, autoincrement=True)
    title = db.Column(VARCHAR(32), nullable = False)
    body = db.Column(VARCHAR(128), server_default="No additional description provided for this rule.")
    author = db.Column(INTEGER, nullable = False)

    time_created = db.Column(TIMESTAMP, nullable = False)

    ### Relationships ###
    forum : Mapped["Forum"] = db.relationship("Forum", back_populates="rules", lazy="select")      # M:1

    __table_args__ = (
        PrimaryKeyConstraint("forum_id", "rule_number", name="pk_forum_rules"),
        CheckConstraint("rule_number < 6", "check_max_forum_rules"),
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
    id = db.Column(BIGINT, nullable = False, autoincrement = True)
    author_id = db.Column(BIGINT, db.ForeignKey("users.id"), nullable = False, index=True)
    author_uname = db.Column(VARCHAR(64), db.ForeignKey("users.username"), nullable = False, index=True)
    forum = db.Column(VARCHAR(128), nullable = False)

    # Post statistics
    score = db.Column(INTEGER, default = 0)
    total_comments = db.Column(INTEGER, default = 0)

    # Post details
    title = db.Column(VARCHAR(64), nullable = False, index=True)
    body_text = db.Column(TEXT, nullable = False)
    flair = db.Column(VARCHAR(16), index=True)
    closed = db.Column(BOOLEAN, default=False)
    time_posted = db.Column(TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    saves = db.Column(INTEGER, default=0)
    reports = db.Column(INTEGER, default=0)

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
    id = db.Column(BIGINT, nullable=False, autoincrement = True)
    author_id = db.Column(BIGINT, db.ForeignKey("users.id"), nullable = False, index=True)
    parent_forum = db.Column(INTEGER, nullable = False)

    # Comment details
    time_created = db.Column(TIMESTAMP, nullable = False, server_default=text("CURRENT_TIMESTAMP"))
    body = db.Column(VARCHAR(512), nullable=False)
    parent_post = db.Column(BIGINT, db.ForeignKey("posts.id"), nullable=False, index=True)
    parent_thread = db.Column(BIGINT, db.ForeignKey("comments.id"))
    replying_to = db.Column(BIGINT, db.ForeignKey("comments.id"))
    score = db.Column(INTEGER, default = 0)
    reports = db.Column(INTEGER, default = 0)

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