import secrets
import time
from typing import Sequence
from uuid import uuid4

from redis.asyncio import Redis

from sqlalchemy import func, select, insert, update
from sqlalchemy.ext.asyncio.session import AsyncSession
from datetime import datetime, timedelta
from fastapi import Response
from fastapi.datastructures import URL

from auth_server.config.app_config import AppConfig
from auth_server.models.database import (
    SuspiciousActivity,
    Admin,
    KeyData,
)
from auth_server.security.admin_roles import AdminRole
from auth_server.utils.typing import AdminSessionDict


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


async def report_suspicious_activity(
    session: AsyncSession,
    config: AppConfig,
    synced_store_client: Redis,
    adminID: int,
    desc: str,
    force_logout: bool = True,
) -> None:
    await session.execute(
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
        and (await session.execute(stmt)).scalar_one()
        >= config.ADMIN.MAX_ACTIVITY_LIMIT
    ):
        await session.execute(update(Admin).values(locked=True))
        await synced_store_client.delete(f"admin:{adminID}")

    await session.commit()


# TODO: Swap this out with KeydataRepository/s equivalent method
async def fetch_valid_keys(session: AsyncSession) -> list[str]:
    """Fetch all valid key IDs from database"""
    valid_keys: list[str] = list(
        (await session.execute(select(KeyData.kid).where(KeyData.expired_at.is_(None))))
        .scalars()
        .all()
    )
    if not valid_keys:
        raise RuntimeError("No valid keys found")
    return valid_keys


def create_admin_session(
    admin_id: int,
    session_length: float,
    revival_digest_length: int,
    role: AdminRole,
    session_iteration: int = 1,
    epoch_timestamp: float | None = None,
) -> AdminSessionDict:
    epoch_timestamp = epoch_timestamp or time.time()
    return AdminSessionDict(
        admin_id=admin_id,
        session_id=uuid4().int,
        revival_digest=secrets.token_hex(revival_digest_length),
        epoch_timestamp=epoch_timestamp,
        expiry_timestamp=epoch_timestamp + session_length,
        session_iteration=session_iteration,
        role=role.value,
    )
