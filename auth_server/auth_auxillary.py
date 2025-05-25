from auth_server.models import db, SuspiciousActivity
from redis_manager import SyncedStore
from sqlalchemy import func, select, insert
from datetime import datetime
from flask import current_app

def purge_admin_session(adminID: int, ttl: int) -> None:
    with SyncedStore.pipeline() as pipe:
        pipe.delete(f'admin:{adminID}')
        pipe.set(f'blacklist:{adminID}', 1, ex=ttl, nx=True)
        pipe.execute()


def report_suspicious_activity(adminID: int, desc: str) -> None:
    db.session.execute(insert(SuspiciousActivity)
                       .values(suspect=adminID, description='Incorrect password'))
    db.session.commit()
    current_time: datetime = datetime.now()

    # If too many suspicious attempts, force logout and lock account
    if db.session.execute(func.count(select(SuspiciousActivity).where((SuspiciousActivity.suspect == adminID) & (SuspiciousActivity.time_logged.between(current_time, current_app.config['SUSPICIOUS_LOOKBACK_TIME']))))) >= current_app.config['MAX_ACTIVITY_LIMIT']:
        purge_admin_session(SyncedStore, adminID, current_app.config['PURGE_BROADCAST_TTL'])