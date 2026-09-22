from datetime import UTC, datetime, timedelta
from typing import Sequence

from auxillary.data_structures.uow import MultiRepositoryWorkCoordinator
from fastapi import Response
from fastapi.datastructures import URL

from auth_server.admin.session_manager import AdminSessionManager
from auth_server.config.app_config import AppConfig
from auth_server.repositories.admin import AdminRepository
from auth_server.repositories.suspicious_activity import (
    SuspiciousActivityRepository,
    SuspiciousActivityResult,
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


async def report_suspicious_activity(
    config: AppConfig,
    admin_id: int,
    desc: str,
    suspicious_activity_repository: SuspiciousActivityRepository,
    admin_repository: AdminRepository,
    coordinator: MultiRepositoryWorkCoordinator,
    admin_session_manager: AdminSessionManager,
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
            if (
                existing_session
                := await admin_session_manager.get_admin_session_via_admin_id(admin_id)
            ):
                await admin_session_manager.terminate_session_via_object(
                    existing_session
                )
