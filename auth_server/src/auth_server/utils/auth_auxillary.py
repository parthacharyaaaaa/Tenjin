import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Final, Sequence
from uuid import uuid4

import ecdsa
from auxillary.data_structures.uow import MultiRepositoryWorkCoordinator
from fastapi import Response
from fastapi.datastructures import URL
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio.session import AsyncSession

from auth_server.config.app_config import AppConfig
from auth_server.models.database import (
    KeyData,
)
from auth_server.repositories.admin import AdminRepository
from auth_server.repositories.suspicious_activity import (
    SuspiciousActivityRepository,
    SuspiciousActivityResult,
)
from auth_server.security.admin_roles import AdminRole
from auth_server.utils.typing import AdminSessionDict, HashFunc


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
    config: AppConfig,
    synced_store_client: Redis,
    admin_id: int,
    desc: str,
    suspicious_activity_repository: SuspiciousActivityRepository,
    admin_repository: AdminRepository,
    coordinator: MultiRepositoryWorkCoordinator,
    force_logout: bool = True,
) -> None:
    await suspicious_activity_repository.insert_activity(admin_id, desc)
    current_time: datetime = datetime.now(UTC)
    async with coordinator.multirepo_work_context(
        suspicious_activity_repository, admin_repository
    ):
        activities: list[
            SuspiciousActivityResult
        ] = await suspicious_activity_repository.get_activity_log(
            admin_id, config.ADMIN.MAX_ACTIVITY_LIMIT
        )
        if force_logout and (
            (len(activities) > config.ADMIN.MAX_ACTIVITY_LIMIT)
            or (
                activities[-1].time_logged
                > current_time
                - timedelta(seconds=config.ADMIN.SUSPICIOUS_LOOKBACK_TIME)
            )
        ):
            await admin_repository.set_admin_locked(admin_id, locked=True)
            await synced_store_client.delete(f"admin:{admin_id}")


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


def sign_session(
    session_token: bytes | bytearray, signing_pem: bytes | bytearray, hashfunc: HashFunc
) -> bytes:
    signing_key: ecdsa.SigningKey = ecdsa.SigningKey.from_pem(signing_pem)
    signature: Final[bytes] = signing_key.sign_deterministic(session_token, hashfunc)

    return b".".join((session_token, signature))
