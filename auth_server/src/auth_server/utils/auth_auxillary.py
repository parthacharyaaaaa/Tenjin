from typing import Sequence

from redis import Redis

from sqlalchemy import func, select, insert, update
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from flask import current_app
from fastapi import Response
from fastapi.datastructures import URL

from auth_server.config.app_config import AppConfig
from auth_server.models.database import (
    SuspiciousActivity,
    Admin,
    KeyData,
)


def attach_tokens(
    response: Response,
    access_token: str,
    refresh_token: str,
    access_max_age: int,
    refresh_max_age: int,
    paths: Sequence[URL],
) -> None:
    response.set_cookie(
        key="access",
        value=access_token,
        max_age=access_max_age,
        httponly=True,
    )
    for path in paths:
        response.set_cookie(
            key="refresh",
            value=refresh_token,
            max_age=refresh_max_age,
            httponly=True,
            path=path.path,
        )


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
