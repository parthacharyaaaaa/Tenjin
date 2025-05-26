from auth_server.models import db, SuspiciousActivity, Admin
from auth_server.redis_manager import SyncedStore
from sqlalchemy import func, select, insert, update, cast
from sqlalchemy.dialects.postgresql import TIMESTAMP
from datetime import datetime
from flask import current_app

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