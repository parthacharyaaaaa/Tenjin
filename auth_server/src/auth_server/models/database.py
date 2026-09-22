import datetime

from sqlalchemy import BOOLEAN, INTEGER, TIMESTAMP, VARCHAR, ForeignKey, text
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.schema import CheckConstraint
from sqlalchemy.sql.functions import func

from auth_server.admin.roles import AdminRole
from auth_server.config.constants import MAX_IDENTITY_LENGTH, MIN_IDENTITY_LENGTH
from auth_server.models.database_enums import ADMIN_ROLES


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
        TIMESTAMP, server_default=text("null")
    )
    last_login: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    locked: Mapped[bool] = mapped_column(
        BOOLEAN, nullable=False, server_default=text("false")
    )
    created_by: Mapped[int] = mapped_column(INTEGER, ForeignKey("admins.id_"))

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
        TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    description: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)


class KeyData(Base):
    __tablename__ = "keydata"
    kid: Mapped[str] = mapped_column(VARCHAR(16), primary_key=True)
    alg: Mapped[str] = mapped_column(
        VARCHAR(8), nullable=False, server_default=text("'ES256'")
    )
    curve: Mapped[str] = mapped_column(VARCHAR(16), nullable=False)
    epoch: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        unique=True,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    rotated_out_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP, server_default=text("null"), nullable=True
    )
    expired_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP, server_default=text("null"), nullable=True
    )
    private_pem: Mapped[bytes] = mapped_column(BYTEA, nullable=False, unique=True)
    public_pem: Mapped[bytes] = mapped_column(BYTEA, nullable=False, unique=True)
    manual_rotation: Mapped[bool] = mapped_column(
        BOOLEAN, server_default=text("null"), nullable=True
    )
    rotated_by: Mapped[int] = mapped_column(
        INTEGER, ForeignKey("admins.id_"), index=True, nullable=True
    )
