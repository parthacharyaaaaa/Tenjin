from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import VARCHAR, INTEGER, TIMESTAMP, BOOLEAN, text
from sqlalchemy.dialects.postgresql import ENUM, BYTEA
from enum import Enum
from typing import Any
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
    password_hash: bytes = db.Column(BYTEA, nullable=False)
    password_salt: bytes = db.Column(BYTEA, nullable=False)
    time_deleted: datetime.datetime = db.Column(TIMESTAMP, server_default=text('null'))
    role: str = db.Column(ADMIN_ROLES, nullable=False)
    last_login: datetime.datetime = db.Column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    locked: bool = db.Column(BOOLEAN, nullable=False, server_default=text('false'))
    created_by: int = db.Column(INTEGER, db.ForeignKey('admins.id'))

class SuspiciousActivity(db.Model):
    __tablename__ = 'suspicious_activities'
    id: int = db.Column(INTEGER, primary_key=True, autoincrement=True)
    suspect: int = db.Column(INTEGER, db.ForeignKey('admins.id'), nullable=False)
    time_logged: datetime.datetime = db.Column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    description: str = db.Column(VARCHAR(64), nullable=False)  

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
    manual_rotation: bool = db.Column(BOOLEAN, server_default=text('false'))
    rotated_by: int = db.Column(INTEGER, db.ForeignKey('admins.id'), index=True)

    def __json_like__(self) -> dict[str, Any]:
        '''Return JSON serializable dictionary, excluding private PEM'''
        return {'kid' : self.kid,
                'alg' : self.alg,
                'curve' : self.curve,
                'epoch' : self.epoch.isoformat(),
                'rotated_out_at' : None if not self.rotated_out_at else self.rotated_out_at.isoformat(),
                'expired_at' : None if not self.expired_at else self.expired_at.isoformat(),
                'public_pem' : self.public_pem.decode(),
                'manual_rotation' : self.manual_rotation,
                'rotated_by' : self.rotated_by}