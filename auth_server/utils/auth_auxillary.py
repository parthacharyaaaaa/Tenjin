from redis import Redis

from auth_server.models import db, SuspiciousActivity, Admin, KeyData
from sqlalchemy import func, select, insert, update
from datetime import datetime
from flask import current_app


def report_suspicious_activity(
    synced_store_client: Redis, adminID: int, desc: str, force_logout: bool = True
) -> None:
    db.session.execute(
        insert(SuspiciousActivity).values(suspect=adminID, description=desc)
    )
    current_time: datetime = datetime.now()

    stmt = (
        select(func.count())
        .select_from(SuspiciousActivity)
        .where(
            (SuspiciousActivity.suspect == adminID)
            & (
                SuspiciousActivity.time_logged.between(
                    current_time - current_app.config["SUSPICIOUS_LOOKBACK_TIME"],
                    current_time,
                )
            )
        )
    )

    if (
        force_logout
        and db.session.execute(stmt).scalar()
        >= current_app.config["MAX_ACTIVITY_LIMIT"]
    ):
        db.session.execute(update(Admin).values(locked=True))
        synced_store_client.delete(f"admin:{adminID}")

    db.session.commit()


def fetch_valid_keys() -> list[str]:
    """Fetch all valid key IDs from database"""
    valid_keys: list[str] = list(
        db.session.execute(select(KeyData.kid).where(KeyData.expired_at.is_(None)))
        .scalars()
        .all()
    )
    if not valid_keys:
        raise RuntimeError("No valid keys found")
    return valid_keys
