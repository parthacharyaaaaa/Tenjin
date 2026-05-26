from redis import Redis

from sqlalchemy import func, select, insert, update
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from flask import current_app

from auth_server.config.app_config import AppConfig
from auth_server.models.database import SuspiciousActivity, Admin, KeyData


def report_suspicious_activity(
    session: Session,
    config: AppConfig,
    synced_store_client: Redis,
    adminID: int,
    desc: str,
    force_logout: bool = True,
) -> None:
    session.execute(
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
                    current_time
                    - timedelta(seconds=config.ADMIN.SUSPICIOUS_LOOKBACK_TIME),
                    current_time,
                )
            )
        )
    )

    if (
        force_logout
        and session.execute(stmt).scalar() >= current_app.config["MAX_ACTIVITY_LIMIT"]
    ):
        session.execute(update(Admin).values(locked=True))
        synced_store_client.delete(f"admin:{adminID}")

    session.commit()


def fetch_valid_keys(session: Session) -> list[str]:
    """Fetch all valid key IDs from database"""
    valid_keys: list[str] = list(
        session.execute(select(KeyData.kid).where(KeyData.expired_at.is_(None)))
        .scalars()
        .all()
    )
    if not valid_keys:
        raise RuntimeError("No valid keys found")
    return valid_keys
