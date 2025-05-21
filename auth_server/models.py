from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import VARCHAR, INTEGER, TIMESTAMP, BOOLEAN, text
from sqlalchemy.dialects.postgresql import ENUM, BYTEA
from enum import Enum
import datetime
db = SQLAlchemy()

class AdminRoles(Enum):
    staff: str = 1
    super: str = 2

ADMIN_ROLES = ENUM('staff', 'super', name='admin_roles', create_type=True)

class Admin(db.Model):
    __tablename__ = 'admins'
    id: int = db.Column(INTEGER, primary_key=True, autoincrement=True)
    username: str = db.Column(VARCHAR(64), nullable=False, unique=True)
    role: str = db.Column(ADMIN_ROLES, nullable=False)

class KeyData(db.Model):
    __tablename__ = 'keydata'
    kid: str = db.Column(VARCHAR(16), primary_key=True)
    alg: str = db.Column(VARCHAR(8), nullable=False, server_default=text("'ES256'"))
    curve: str = db.Column(VARCHAR(16), nullable=False)
    epoch: datetime.datetime = db.Column(TIMESTAMP, index=True, nullable=False, unique=True, server_default=text('CURRENT_TIMESTAMP'))
    rotated_out_at: datetime.datetime = db.Column(TIMESTAMP, server_default=text('null'))
    expired_at: datetime.datetime = db.Column(TIMESTAMP, server_default=text('null'))
    private_pem: bytes = db.Column(BYTEA, nullable=False, unique=True)
    public_pem: bytes = db.Column(BYTEA, nullable=False, unique=True)
    manual_rotation: bool = db.Column(BOOLEAN, server_default=text('false'), index=True)

