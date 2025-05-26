from auth_server.models import db, SuspiciousActivity, Admin
from auth_server.redis_manager import SyncedStore
from sqlalchemy import func, select, insert, update
from werkzeug.exceptions import Unauthorized, Forbidden
from datetime import datetime
from flask import current_app, request, g
from functools import wraps
import time
import ujson
import base64

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

def admin_only(endpoint):
    '''
        #### Ensure that an incoming request carries with itself the necessary admin session token.
        - Verifies token semantics (session ID, admin ID, and expiry claims), 
        - Checks session expiry
        - Checks session details against session hashmap in Synced Store (session ID, expiry, role)

        On success, the session mapping is assigned to global context as `g.SESSION_TOKEN`, else performs the necessary account locking and session deletion
    '''
    @wraps(endpoint)
    def decorated(*args, **kwargs):
        encodedSessionToken: dict = request.headers.get('X-SESSION-TOKEN', None)
        if not encodedSessionToken:
            raise Unauthorized('Missing session token')
        
        sessionToken: dict = ujson.loads(base64.urlsafe_b64decode(encodedSessionToken).decode())
        # Verify token semantics
        sessionID, adminID, expiry, role = sessionToken.get('session_id'), sessionToken.get('admin_id'), sessionToken.get('expiry_at'), sessionToken.get('role')
        if not adminID:
            raise Unauthorized("Invalid token")
        
        adminID: int = int(adminID)
        adminSessionKey: str = f'admin:{adminID}'
        if not (sessionID and expiry):
            SyncedStore.delete(adminSessionKey)
            report_suspicious_activity(adminID, 'Invalid token submitted')
            raise Unauthorized("Invalid token")
        
        # Verify token expiry
        if time.time() > expiry:
            SyncedStore.delete(adminSessionKey)
            raise Forbidden("Session expired, please login again")

        # Verify whether session exists
        adminSessionMapping: dict = SyncedStore.hgetall(adminSessionKey)
        if not adminSessionMapping:
            report_suspicious_activity(adminID, 'No active session found')
            raise Unauthorized('No session for this admin exists')
            
        # Session exists, check credentials
        if not (sessionID == int(adminSessionMapping.get(b'session_id')) and 
                expiry == float(adminSessionMapping.get(b'expiry_at')) and 
                role == adminSessionMapping.get(b'role').decode()):
            report_suspicious_activity(adminID, 'Invalid session token')
            raise Unauthorized('Invalid session token')
        
        g.SESSION_TOKEN = sessionToken
    
        return endpoint(*args, **kwargs)
    return decorated