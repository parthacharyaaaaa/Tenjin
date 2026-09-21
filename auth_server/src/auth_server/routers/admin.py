import hmac
from datetime import UTC, datetime
from typing import Annotated, Final

from auxillary.data_structures.uow import MultiRepositoryWorkCoordinator
from auxillary.utils import (
    bcrypt_check_password,
    bcrypt_hash_password,
    generic_database_fetch_exception,
    json_repr,
)
from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from auth_server.config.app_config import AppConfig
from auth_server.dependencies import (
    get_admin_repository,
    get_admin_session_manager,
    get_app_config,
    get_repository_work_coordinator,
    get_suspicious_activity_repository,
)
from auth_server.models.cmd_requests import (
    AdminAuthenticationModel,
    AdminIdentificationModel,
    AdminRefreshModel,
)
from auth_server.models.session import AdminSession
from auth_server.repositories.admin import (
    AdminPublicResult,
    AdminRepository,
)
from auth_server.repositories.suspicious_activity import SuspiciousActivityRepository
from auth_server.security.admin_roles import AdminRole
from auth_server.security.admin_sessions import AdminSessionManager
from auth_server.security.permissions import Permission
from auth_server.utils.auth_auxillary import (
    report_suspicious_activity,
)
from auth_server.utils.dependencies import get_admin_session, require_permissions

ADMIN: Final[APIRouter] = APIRouter()


@ADMIN.post("/admins/login")
async def admin_login(
    auth_model: AdminAuthenticationModel,
    config: Annotated[AppConfig, Depends(get_app_config)],
    admin_repository: Annotated[AdminRepository, Depends(get_admin_repository)],
    suspicious_activity_repository: Annotated[
        SuspiciousActivityRepository, Depends(get_suspicious_activity_repository)
    ],
    repository_coordinator: Annotated[
        MultiRepositoryWorkCoordinator, Depends(get_repository_work_coordinator)
    ],
    admin_session_manager: Annotated[
        AdminSessionManager, Depends(get_admin_session_manager)
    ],
) -> JSONResponse:
    try:
        admin: AdminPublicResult | None = await admin_repository.get_admin_by_username(
            auth_model.identity, include_deleted=True
        )

        if not admin:
            raise HTTPException(
                404, f"No admin with identity {auth_model.identity} found"
            )

        if admin.time_deleted is not None:
            raise HTTPException(410, f"Admin {admin.username} has been deleted")

        if admin.locked:
            await report_suspicious_activity(
                config,
                admin.id_,
                "Attempt to log into a locked account",
                suspicious_activity_repository,
                admin_repository,
                repository_coordinator,
                admin_session_manager,
                force_logout=False,
            )
            raise HTTPException(
                403,
                "This account is currently locked on grounds of suspicious activities",
            )
    except SQLAlchemyError:
        raise HTTPException(500, "Failed to fetch admin information")

    if not bcrypt_check_password(auth_model.password, admin.password_hash):
        await report_suspicious_activity(
            config,
            admin.id_,
            "Incorrect password",
            suspicious_activity_repository,
            admin_repository,
            repository_coordinator,
            admin_session_manager,
            force_logout=False,
        )
        raise HTTPException(401, "Incorrect passwword")

    try:
        await admin_repository.update_last_login(admin.id_)
    except SQLAlchemyError as e:
        raise HTTPException(500, "An error occured when logging you in") from e

    # Exists in DB, check synced_store_client to see if session is already active
    try:
        # Single sign-in policy, invalidate existing session and add entry in logs
        if (
            existing_session
            := await admin_session_manager.get_admin_session_via_admin_id(admin.id_)
        ):
            # await admin_session_manager.terminate_session(existing_session.session_id, admin.id_)
            await report_suspicious_activity(
                config,
                admin.id_,
                "Session already active",
                suspicious_activity_repository,
                admin_repository,
                repository_coordinator,
                admin_session_manager,
                force_logout=False,
            )
            session_token, revival_digest = await admin_session_manager.refresh_session(
                existing_session
            )
        else:
            (
                session_token,
                revival_digest,
            ) = await admin_session_manager.initialize_session(
                admin.id_, AdminRole(admin.role)
            )
    except RedisError as e:
        raise HTTPException(500, "Failed to perform login") from e

    return JSONResponse(
        {"session_token": session_token, "revival_digest": revival_digest}
    )


@ADMIN.delete("/admins")
async def admin_delete(
    deletion_model: AdminIdentificationModel,
    admin_session: Annotated[
        AdminSession, Depends(require_permissions(Permission.DELETE_ADMIN))
    ],
    admin_repository: Annotated[AdminRepository, Depends(get_admin_repository)],
) -> JSONResponse:
    try:
        admin: AdminPublicResult | None = await admin_repository.get_admin(
            deletion_model.id_, include_deleted=True
        )
    except SQLAlchemyError:
        generic_database_fetch_exception()
    if not admin:
        raise HTTPException(404, f"No admin with ID {deletion_model.id_} found")
    if admin.time_deleted:
        raise HTTPException(
            410, f"Admin {admin.username} (ID: {admin.id_}) already deleted"
        )

    deletion_time: datetime = datetime.now(UTC)
    try:
        await admin_repository.delete_admin(deletion_model.id_, deletion_time)
    except Exception as e:
        raise HTTPException(500, "Failed to delete admin account") from e

    return JSONResponse(
        {
            "message": "Admin deleted",
            "admin_id": admin.id_,
            "admin_username": admin.username,
            "time_deleted": deletion_time,
        }
    )


@ADMIN.post("/admins/refresh")
async def admin_refresh(
    refresh_model: AdminRefreshModel,
    admin_session: Annotated[AdminSession, Depends(get_admin_session)],
    config: Annotated[AppConfig, Depends(get_app_config)],
    admin_repository: Annotated[AdminRepository, Depends(get_admin_repository)],
    suspicious_activity_repository: Annotated[
        SuspiciousActivityRepository, Depends(get_suspicious_activity_repository)
    ],
    repository_coordinator: Annotated[
        MultiRepositoryWorkCoordinator, Depends(get_repository_work_coordinator)
    ],
    admin_session_manager: Annotated[
        AdminSessionManager, Depends(get_admin_session_manager)
    ],
) -> JSONResponse:
    """
    Refresh an admin's session and enforce a maximum number of times
    a session can be refreshed before requiring reauthentication
    """
    if admin_session.iteration >= config.ADMIN.MAX_SESSION_ITERATIONS:
        raise HTTPException(
            409,
            " ".join(
                (
                    "Maximum session reiterations reached,",
                    "please reauthenticate to be",
                    "assigned a fresh session",
                )
            ),
        )
    if not hmac.compare_digest(
        admin_session.revival_digest, refresh_model.refresh_digest
    ):
        await report_suspicious_activity(
            config,
            refresh_model.id_,
            "Invalid session revival digest",
            suspicious_activity_repository,
            admin_repository,
            repository_coordinator,
            admin_session_manager,
        )
        raise HTTPException(403, "Invalid revival digest provided")

    session_token, revival_digest = await admin_session_manager.refresh_session(
        admin_session
    )

    return JSONResponse(
        {"session_token": session_token, "revival_digest": revival_digest}
    )


@ADMIN.patch("/admins/logout")
async def admin_logout(
    admin_session: Annotated[AdminSession, Depends(get_admin_session)],
    admin_session_manager: Annotated[
        AdminSessionManager, Depends(get_admin_session_manager)
    ],
) -> JSONResponse:
    await admin_session_manager.terminate_session_via_object(admin_session)
    return JSONResponse({"message": "Logout successful"})


@ADMIN.post("/admins/locks")
async def admin_lock(
    request: Request,
    admin_session: Annotated[AdminSession, Depends(get_admin_session)],
    identification_model: AdminIdentificationModel,
    admin_repository: Annotated[AdminRepository, Depends(get_admin_repository)],
    admin_session_manager: Annotated[
        AdminSessionManager, Depends(get_admin_session_manager)
    ],
) -> JSONResponse:
    """Lock a staff admin's account"""
    try:
        admin: AdminPublicResult | None = await admin_repository.get_admin(
            identification_model.id_
        )
    except SQLAlchemyError:
        generic_database_fetch_exception()

    if not admin:
        raise HTTPException(
            404, f"No admin with id {identification_model.id_} could be found"
        )
    if admin.locked:
        conflict: HTTPException = HTTPException(409, "Admin account is already locked")
        setattr(
            conflict,
            "kwargs",
            {
                "links": {
                    "unlock admin account": {"_href": request.url_for("admin_unlock")}
                }
            },
        )
        raise conflict

    try:
        await admin_repository.set_admin_locked(admin.id_, True)
    except SQLAlchemyError:
        raise HTTPException(
            500, f"Failed to lock admin {admin.username} (ID: {admin.id_})"
        )

    try:
        if (
            existing_session
            := await admin_session_manager.get_admin_session_via_admin_id(admin.id_)
        ):
            await admin_session_manager.terminate_session_via_object(existing_session)
    except RedisError:
        raise HTTPException(
            500, f"Failed to delete active session for locked admin: {admin.username}"
        )
    return JSONResponse({"message": "Admin locked succesfully"})


@ADMIN.delete("/admins/locks")
async def admin_unlock(
    request: Request,
    identification_model: AdminIdentificationModel,
    admin_repository: Annotated[AdminRepository, Depends(get_admin_repository)],
) -> JSONResponse:
    """Unlock a staff admin's account"""
    try:
        admin: AdminPublicResult | None = await admin_repository.get_admin(
            identification_model.id_
        )
    except SQLAlchemyError:
        generic_database_fetch_exception()

    if not admin:
        raise HTTPException(
            404, f"No admin with id {identification_model.id_} could be found"
        )
    if not admin.locked:
        conflict: HTTPException = HTTPException(
            409, "Admin account is already unlocked"
        )
        setattr(
            conflict,
            "kwargs",
            {
                "links": {
                    "unlock admin account": {"_href": request.url_for("admin_unlock")}
                }
            },
        )
        raise conflict

    try:
        await admin_repository.set_admin_locked(admin.id_, False)
    except SQLAlchemyError:
        raise HTTPException(
            500, f"Failed to unlock admin {admin.username} (ID: {admin.id_})"
        )

    return JSONResponse({"message": "Admin unlocked succesfully"})


@ADMIN.post("/admins")
async def create_admin(
    admin_model: AdminAuthenticationModel,
    admin_session: Annotated[
        AdminSession, Depends(require_permissions(Permission.CREATE_ADMIN))
    ],
    admin_repository: Annotated[AdminRepository, Depends(get_admin_repository)],
) -> JSONResponse:
    try:
        existing_admin: (
            AdminPublicResult | None
        ) = await admin_repository.get_admin_by_username(admin_model.identity)
    except SQLAlchemyError:
        generic_database_fetch_exception()
    if existing_admin:
        raise HTTPException(
            409, f"Admin with username {admin_model.identity} already exists"
        )

    pw_hash: Final[bytes] = bcrypt_hash_password(admin_model.password)
    try:
        admin: AdminPublicResult = await admin_repository.create_admin(
            username=admin_model.identity,
            password_hash=pw_hash,
            role=AdminRole.STAFF,
            creation_author=admin_session.admin_id,
            returning=True,
        )
    except SQLAlchemyError as e:
        raise HTTPException(500, "Failed to create a new admin") from e

    return JSONResponse({"message": "Admin created", "admin": json_repr(admin)}, 202)
