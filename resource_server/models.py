from resource_server import db

from sqlalchemy import UniqueConstraint, PrimaryKeyConstraint, Index, CheckConstraint, Extract, ForeignKeyConstraint
from sqlalchemy.dialects.postgresql import TIMESTAMP, BYTEA
from sqlalchemy.types import INTEGER, NUMERIC, BOOLEAN, VARCHAR, BIGINT, TEXT

class User(db.Model):
    __tablename__ = "users"

    # Basic identification
    id = db.Column(BIGINT, nullable = False, autoincrement=True)
    username = db.Column(VARCHAR(64), nullable = False)
    _alias = db.Column(VARCHAR(64), nullable = True)
    email = db.Column(VARCHAR(64), nullable = False)

    pfp = db.Column(VARCHAR(256))

    # Passwords and salts
    pw_hash = db.Column(BYTEA(256), nullable = False)
    ps_salt = db.Column(BYTEA(64), nullable = False)

    # Activity
    aura = db.Column(BIGINT, default = 0)
    total_posts = db.Column(INTEGER, default = 0)
    total_comments = db.Column(INTEGER, default = 0)
    date_joined = db.Column(TIMESTAMP, nullable = False)
    # tenjin_age = db.Column()
    last_login = db.Column(TIMESTAMP)

    __table_args__ = (
        PrimaryKeyConstraint(id, name="pk_users_id"),
        UniqueConstraint(username, name="uq_users_username"),
        UniqueConstraint(email, name="uq_users_email"),
        Index("idx_users_email", email),
        Index("idx_users_username", username),
        CheckConstraint("LENGTH(username) > 5", name="ck_users_username_length"),
        CheckConstraint("_alias IS NULL OR LENGTH(_alias) > 5", name="ck_users_alias_length"),
        CheckConstraint(r"email ~*'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'", name="ck_users_email_regex"),
    )

class Forum(db.Model):
    __tablename__ = "forums"

    # Basic identification
    id = db.Column(INTEGER, nullable = False, autoincrement = True)
    _name = db.Column(VARCHAR(64), nullable = False)

    # Appearance
    color_theme = db.Column(NUMERIC(2), nullable = False, default = 1)
    pfp = db.Column(VARCHAR(128))
    description = db.Column(VARCHAR(256))

    # Activity stats
    subscribers = db.Column(BIGINT, nullable = False, default = 0)
    posts = db.Column(BIGINT, nullable = False, default = 0)
    highlight_post_1 = db.Column(BIGINT, nullable = True)
    highlight_post_2 = db.Column(BIGINT, nullable = True)
    highlight_post_3 = db.Column(BIGINT, nullable = True)

    created_at = db.Column(TIMESTAMP, nullable = False)
    admin_count = db.Column(NUMERIC(3), default = 1)

    __table_args__ = (
        PrimaryKeyConstraint(id, name="pk_forums_id"),
        UniqueConstraint(_name, name="uq_forums_name"),
        CheckConstraint("posts >= 0", name="check_posts_value"),
        CheckConstraint("subscribers >= 0", name="check_subs_values"),
        CheckConstraint("color_theme > 0 AND color_theme < 20", name="limit_color_themes"),
        CheckConstraint("admin_count > 0", name="check_atleast_1_admin"),
        Index(_name, name="idx_forums_name"),
    )

class Forum_Rules(db.Model):
    __tablename__ = "forum_rules"

    #Identification
    forum_id = db.Column(INTEGER, nullable = False)
    
    # Data
    rule_number = db.Column(NUMERIC(1), nullable = False)
    title = db.Column(VARCHAR(32), nullable = False)
    body = db.Column(VARCHAR(64))
    author = db.Column(INTEGER, nullable = False)

    time_created = db.Column(TIMESTAMP, nullable = False)

    __table_args__ = (
        PrimaryKeyConstraint(forum_id, rule_number, name="pk_forum_rules"),
    )

class Post(db.Model):
    __tablename__ = "posts"

    # Basic identification
    id = db.Column(BIGINT, nullable = False, autoincrement = True)
    author_id = db.Column(VARCHAR(64), nullable = False)
    author_uname = db.Column(VARCHAR(64), nullable = False)
    forum = db.Column(VARCHAR(128), nullable = False)

    # Post statistics
    score = db.Column(INTEGER, default = 0)
    total_comments = db.Column(INTEGER, default = 0)

    # Post details
    title = db.Column(VARCHAR(64), nullable = False)
    body_text = db.Column(TEXT, nullable = False)
    flair = db.Column(VARCHAR(16))
    closed = db.Column(BOOLEAN, default=False)
    time_posted = db.Column(TIMESTAMP, nullable=False)
    saves = db.Column(INTEGER, default=0)
    reports = db.Column(INTEGER, default=0)

    __table_args__ = (
        PrimaryKeyConstraint(id, name="pk_posts_id"),
        ForeignKeyConstraint(author_id, User.id, name="fk_posts_author_id_users_id"),
        ForeignKeyConstraint(author_uname, User.username, name="fk_posts_author_uname_users_username"),
        Index("idx_posts_author_id", author_id),
        Index("idx_posts_author_name", author_uname),
        Index("idx_posts_title", title),
    )