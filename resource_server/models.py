from resource_server import db

from sqlalchemy import UniqueConstraint, PrimaryKeyConstraint, Index, CheckConstraint, Extract
from sqlalchemy.dialects.postgresql import TIMESTAMP, BYTEA
from sqlalchemy.types import INTEGER, NUMERIC, BOOLEAN, VARCHAR, BIGINT

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