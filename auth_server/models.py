import datetime
from enum import Enum
from typing import Any

from sqlalchemy import VARCHAR, INTEGER, TIMESTAMP, BOOLEAN, ForeignKey, text
from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped
from sqlalchemy.dialects.postgresql import ENUM, BYTEA


class Base(DeclarativeBase):
    pass


class AdminRoles(Enum):
    staff = 1
    super = 2


ADMIN_ROLES = ENUM("staff", "super", name="admin_roles", create_type=True)


class Admin(Base):
    __tablename__ = "admins"

    id_: Mapped[int] = mapped_column(INTEGER, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(VARCHAR(64), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(ADMIN_ROLES, nullable=False)

    password_hash: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    password_salt: Mapped[bytes] = mapped_column(BYTEA, nullable=False)

    time_deleted: Mapped[datetime.datetime | None] = mapped_column(
        TIMESTAMP, server_default=text("null")
    )
    last_login: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    locked: Mapped[bool] = mapped_column(
        BOOLEAN, nullable=False, server_default=text("false")
    )
    created_by: Mapped[int] = mapped_column(INTEGER, ForeignKey("admins.id"))


class SuspiciousActivity(Base):
    __tablename__ = "suspicious_activities"

    id_: Mapped[int] = mapped_column(INTEGER, primary_key=True, autoincrement=True)
    suspect: Mapped[int] = mapped_column(
        INTEGER, ForeignKey("admins.id"), nullable=False
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
        TIMESTAMP, server_default=text("null")
    )
    expired_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP, server_default=text("null")
    )
    private_pem: Mapped[bytes] = mapped_column(BYTEA, nullable=False, unique=True)
    public_pem: Mapped[bytes] = mapped_column(BYTEA, nullable=False, unique=True)
    manual_rotation: Mapped[bool] = mapped_column(BOOLEAN, server_default=text("false"))
    rotated_by: Mapped[int] = mapped_column(
        INTEGER, ForeignKey("admins.id"), index=True
    )

    def __json_like__(self) -> dict[str, Any]:
        """Return JSON serializable dictionary, excluding private PEM"""
        return {
            "kid": self.kid,
            "alg": self.alg,
            "curve": self.curve,
            "epoch": self.epoch.isoformat(),
            "rotated_out_at": (
                None if not self.rotated_out_at else self.rotated_out_at.isoformat()
            ),
            "expired_at": None if not self.expired_at else self.expired_at.isoformat(),
            "public_pem": self.public_pem.decode(),
            "manual_rotation": self.manual_rotation,
            "rotated_by": self.rotated_by,
        }
