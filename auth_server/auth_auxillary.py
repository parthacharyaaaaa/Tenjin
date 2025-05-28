from auth_server.models import db, SuspiciousActivity, Admin, KeyData
from auth_server.redis_manager import SyncedStore
from sqlalchemy import func, select, insert, update
from werkzeug.exceptions import Unauthorized, Forbidden
from datetime import datetime
from flask import current_app, request, g
from functools import wraps
import time
import ujson
import base64
from typing import Literal

def report_suspicious_activity(adminID: int, desc: str, force_logout: bool = True) -> None:
    db.session.execute(insert(SuspiciousActivity)
                       .values(suspect=adminID, description='Incorrect password'))
    current_time: datetime = datetime.now()

    stmt = select(func.count()).select_from(SuspiciousActivity).where(
        (SuspiciousActivity.suspect == adminID) &
        (SuspiciousActivity.time_logged.between(current_time-current_app.config['SUSPICIOUS_LOOKBACK_TIME'], current_time))
    )

    if force_logout and db.session.execute(stmt).scalar() >= current_app.config['MAX_ACTIVITY_LIMIT']:
        db.session.execute(update(Admin).values(locked=True))
        SyncedStore.delete(f'admin:{adminID}')

    db.session.commit()

def fetch_valid_keys() -> list[str]:
    '''Fetch all valid key IDs from database'''
    _res = db.session.execute(select(KeyData.kid).where(KeyData.expired_at.is_(None)))
    if not _res:
        raise RuntimeError("No valid keys found")
    return _res.scalars().all()

def admin_only(required_role: Literal['staff', 'super'] = 'staff'):
    '''
    #### Role-based admin session validation decorator.
    - Verifies token presence and semantics
    - Checks expiry
    - Validates session from SyncedStore
    - Compares role and ensures it meets or exceeds the required role

    On success, attaches session token to `g.SESSION_TOKEN`
    '''
    role_hierarchy = {'staff': 1, 'super': 2}

    def wrapper(endpoint):
        @wraps(endpoint)
        def decorated(*args, **kwargs):
            encodedSessionToken: str = request.headers.get('X-SESSION-TOKEN', None)
            if not encodedSessionToken:
                raise Unauthorized('Missing session token')
            
            try:
                sessionToken: dict = ujson.loads(base64.urlsafe_b64decode(encodedSessionToken).decode())
            except Exception:
                raise Unauthorized("Malformed session token")
            
            sessionID = sessionToken.get('session_id')
            adminID = sessionToken.get('admin_id')
            expiry = sessionToken.get('expiry_at')
            role = sessionToken.get('role')
            iteration = sessionToken.get('session_iteration')

            if not adminID:
                raise Unauthorized("Invalid token")

            adminID = int(adminID)
            adminSessionKey = f'admin:{adminID}'

            if not (sessionID and expiry):
                SyncedStore.delete(adminSessionKey)
                report_suspicious_activity(adminID, 'Invalid token submitted')
                raise Unauthorized("Invalid token")

            if time.time() > expiry:
                SyncedStore.delete(adminSessionKey)
                raise Forbidden("Session expired, please login again")

            adminSessionMapping = SyncedStore.hgetall(adminSessionKey)
            if not adminSessionMapping:
                report_suspicious_activity(adminID, 'No active session found')
                raise Unauthorized('No session for this admin exists')

            if not (sessionID == int(adminSessionMapping.get(b'session_id')) and 
                    expiry == float(adminSessionMapping.get(b'expiry_at')) and 
                    role == adminSessionMapping.get(b'role').decode() and
                    iteration == int(adminSessionMapping.get(b'session_iteration'))):
                report_suspicious_activity(adminID, 'Invalid session token')
                raise Unauthorized('Invalid session token')

            actual_role = role
            if role_hierarchy.get(actual_role, 0) < role_hierarchy[required_role]:
                raise Forbidden("Insufficient permissions for this action")

            g.SESSION_TOKEN = sessionToken
            return endpoint(*args, **kwargs)
        return decorated
    return wrapper