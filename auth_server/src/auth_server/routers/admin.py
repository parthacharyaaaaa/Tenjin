import base64
import secrets
import time
from datetime import datetime
from typing import Annotated, Final
from uuid import uuid4

from fastapi import APIRouter, Depends
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from fastapi.requests import Request

import orjson

from redis.asyncio import Redis
from redis.exceptions import RedisError

from sqlalchemy import select, update, insert
from sqlalchemy.ext.asyncio.session import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql import and_

from auxillary.utils import (
    genericDBFetchException,
    verify_password,
    hash_password,
)

from auth_server.config.app_config import AppConfig
from auth_server.config.constants import REVIVAL_DIGEST_LENGTH
from auth_server.dependencies import (
    get_app_config,
    get_synced_store_client,
    get_database_session,
)
from auth_server.models.cmd_requests import (
    AdminAuthenticationModel,
    AdminIdentificationModel,
    AdminRefreshModel,
)
from auth_server.models.database import Admin
from auth_server.models.session import AdminSession
from auth_server.security.admin_roles import AdminRole
from auth_server.security.permissions import Permission
from auth_server.utils.auth_auxillary import report_suspicious_activity
from auth_server.utils.dependencies import require_permissions, validate_admin_session

ADMIN: Final[APIRouter] = APIRouter()


@ADMIN.post("/admins/login")
async def admin_login(
    auth_model: AdminAuthenticationModel,
    config: Annotated[AppConfig, Depends(get_app_config)],
    synced_store_client: Annotated[Redis, Depends(get_synced_store_client)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> JSONResponse:
    admin: Admin | None = None
    try:
        admin = (
            await session.execute(
                select(Admin).where(
                    and_(
                        Admin.username == auth_model.identity,
                        Admin.time_deleted == None,
                    )
                )
            )
        ).scalar_one_or_none()

        if not admin:
            raise HTTPException(404, "No admin with these credentials found")

        if admin.locked:
            await report_suspicious_activity(
                session,
                config,
                synced_store_client,
                admin.id_,
                "Attempt to log into a locked account",
                force_logout=False,
            )
            raise HTTPException(
                403,
                "This account is currently locked on grounds of suspicious activities",
            )
    except SQLAlchemyError:
        raise Exception

    if not verify_password(
        auth_model.password, admin.password_hash, admin.password_salt
    ):
        await report_suspicious_activity(
            session,
            config,
            synced_store_client,
            admin.id_,
            "Incorrect password",
            force_logout=False,
        )
        raise HTTPException(401, "Incorrect passwword")

    # Exists in DB, check synced_store_client to see if session is already active
    session_key: Final[str] = f"admin:{admin.id_}"
    try:
        admin_session: dict[str, str] = synced_store_client.hgetall(session_key)  # type: ignore[reportAssignmentType]

        # Single sign-in policy, invalidate existing session and add entry in logs
        if admin_session:
            synced_store_client.delete(session_key)
            await report_suspicious_activity(
                session,
                config,
                synced_store_client,
                admin.id_,
                "Session already active",
                force_logout=False,
            )
            raise HTTPException(
                409, "An admin session with these credentials is already active"
            )

    except RedisError:
        raise HTTPException(500, "An error occured when validating session integrity")

    try:
        await session.execute(update(Admin).values(last_login=datetime.now()))
    except SQLAlchemyError:
        raise HTTPException(500, "An error occured when logging you in")

    # Admin validated, create new session
    sessionID: int = uuid4().int
    revival_digest: str = secrets.token_hex(REVIVAL_DIGEST_LENGTH)
    epoch: float = time.time()
    expiry: float = epoch + config.ADMIN.ADMIN_SESSION_DURATION
    sessionMapping: dict = {
        "admin_id": admin.id_,
        "session_id": sessionID,
        "session_iteration": 1,
        "revival_digest": revival_digest,
        "epoch": epoch,
        "expiry_at": expiry,
        "role": admin.role,
    }

    synced_store_client.hset(session_key, mapping=sessionMapping)
    sessionMapping.pop("revival_digest")
    sessionMapping["message"] = "Login successful"

    encoded_session_token: str = base64.urlsafe_b64encode(
        orjson.dumps(sessionMapping)
    ).decode()
    return JSONResponse(
        {"session_token": encoded_session_token, "revival_digest": revival_digest}
    )


@ADMIN.delete("/admins")
async def admin_delete(
    deletion_model: AdminIdentificationModel,
    admin_session: Annotated[
        AdminSession, Depends(require_permissions(Permission.DELETE_ADMIN))
    ],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> JSONResponse:
    try:
        admin: Admin | None = (
            await session.execute(
                select(Admin).where(
                    (Admin.id_ == deletion_model.id_) & (Admin.time_deleted == None)
                )
            )
        ).scalar_one_or_none()
        if not admin:
            raise HTTPException(404, f"No admin with ID {deletion_model.id_} found")

    except SQLAlchemyError:
        genericDBFetchException()

    try:
        await session.execute(
            update(Admin)
            .where(Admin.id_ == deletion_model.id_)
            .values(time_deleted=datetime.now())
        )
        await session.commit()
    except:
        raise HTTPException(500, "Failed to delete admin account")

    return JSONResponse({"message": "Admin deleted"})


@ADMIN.post("/admins/refresh")
async def admin_refresh(
    refresh_model: AdminRefreshModel,
    admin_session: Annotated[AdminSession, Depends(validate_admin_session)],
    config: Annotated[AppConfig, Depends(get_app_config)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    synced_store_client: Annotated[Redis, Depends(get_synced_store_client)],
) -> JSONResponse:
    """Refresh an admin's session and enforce a maximum number of times a session can be refreshed before requiring reauthentication"""
    # TODO: Move refresh-digest to request headers
    admin_key: Final[str] = f"admin:{refresh_model.id_}"
    if admin_session.iteration >= config.ADMIN.MAX_SESSION_ITERATIONS:
        synced_store_client.delete(admin_key)
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

    actual_digest_bytes: bytes = synced_store_client.hget(admin_key, "revival_digest")  # type: ignore[reportAssignmentType]
    if not actual_digest_bytes:
        synced_store_client.delete(admin_key)
        raise HTTPException(
            500, "An error occured in verifying revival digests. Please reuthenticate"
        )

    if actual_digest_bytes == b"__NF__":
        synced_store_client.delete(admin_key)
        raise HTTPException(409, "Maximum session reiterations reached")

    if actual_digest_bytes.decode() != refresh_model.refresh_digest:
        await report_suspicious_activity(
            session,
            config,
            synced_store_client,
            refresh_model.id_,
            "Invalid session revival digest",
        )
        raise HTTPException(403, "Invalid revival digest provided")

    # Given digest matches revival digest. Refresh session and generate a new revival digest
    new_iteration: int = admin_session.iteration + 1
    new_session_id: int = uuid4().int
    epoch: float = time.time()

    expiry: float = epoch + config.ADMIN.ADMIN_SESSION_DURATION
    revival_digest: bytes | str = (
        secrets.token_hex(256)
        if new_iteration == config.ADMIN.MAX_SESSION_ITERATIONS
        else "__END__"
    )
    newSessionMapping: dict[str, str | float] = {
        "admin_id": refresh_model.id_,
        "session_id": new_session_id,
        "session_iteration": new_iteration,
        "revival_digest": revival_digest,
        "epoch": epoch,
        "expiry_at": expiry,
        "role": admin_session.role.value,
    }

    synced_store_client.hset(admin_key, mapping=newSessionMapping)
    newSessionMapping.pop("revival_digest")

    newSessionMapping["message"] = "Session extended"

    encoded_session_token: str = base64.urlsafe_b64encode(
        orjson.dumps(newSessionMapping)
    ).decode()

    return JSONResponse(
        {"session_token": encoded_session_token, "revival_digest": revival_digest}
    )


@ADMIN.patch("/admins/logout")
def admin_logout(
    identification_model: AdminIdentificationModel,
    synced_store_client: Annotated[Redis, Depends(get_synced_store_client)],
) -> JSONResponse:
    synced_store_client.delete(f"admin:{identification_model.id_}")
    return JSONResponse({"message": "Logout successful"})


@ADMIN.post("/admins/locks")
async def admin_lock(
    request: Request,
    identification_model: AdminIdentificationModel,
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> JSONResponse:
    """Lock a staff admin's account"""
    try:
        admin: Admin | None = (
            await session.execute(
                select(Admin)
                .where(Admin.id_ == identification_model.id_)
                .with_for_update(key_share=True)
            )
        ).scalar_one_or_none()

        if not admin:
            raise HTTPException(
                404, f"No admin with id {identification_model.id_} could be found"
            )
        if admin.locked:
            conflict: HTTPException = HTTPException(
                409, "Admin account is already locked"
            )
            setattr(
                conflict,
                "kwargs",
                {
                    "links": {
                        "unlock admin account": {
                            "_href": request.url_for("admin_unlock")
                        }
                    }
                },
            )
            raise conflict

        await session.execute(
            update(Admin)
            .where(Admin.id_ == identification_model.id_)
            .values(locked=True)
        )
        await session.commit()
    except SQLAlchemyError:
        raise Exception

    # Log out the target admin
    synced_store_client: Final[Redis] = get_synced_store_client()
    synced_store_client.delete(f"admin:{identification_model.id_}")

    return JSONResponse({"message": "Admin locked succesfully"})


@ADMIN.delete("/admins/locks")
async def admin_unlock(
    request: Request,
    identification_model: AdminIdentificationModel,
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> JSONResponse:
    """Unlock a staff admin's account"""
    try:
        admin: Admin | None = (
            await session.execute(
                select(Admin)
                .where(Admin.id_ == identification_model.id_)
                .with_for_update()
            )
        ).scalar_one_or_none()

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
                        "lock admin account": {"_href": request.url_for("admin_lock")}
                    }
                },
            )
            raise conflict

        await session.execute(
            update(Admin)
            .where(Admin.id_ == identification_model.id_)
            .values(locked=False)
        )
        await session.commit()
    except SQLAlchemyError:
        genericDBFetchException()

    return JSONResponse({"message": "Admin unlocked succesfully"})


@ADMIN.post("/admins")
async def create_admin(
    admin_model: AdminAuthenticationModel,
    admin_session: Annotated[
        AdminSession, Depends(require_permissions(Permission.CREATE_ADMIN))
    ],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> JSONResponse:
    try:
        existing_admin_id: int | None = (
            await session.execute(
                select(Admin.id_).where(Admin.username == admin_model.identity)
            )
        ).scalar_one_or_none()

        if existing_admin_id:
            raise HTTPException(409, "An admin with this suername already exists")
    except SQLAlchemyError:
        genericDBFetchException()

    pw_hash, pw_salt = hash_password(admin_model.password)
    try:
        await session.execute(
            insert(Admin).values(
                username=admin_model.identity,
                password_hash=pw_hash,
                password_salt=pw_salt,
                role=AdminRole.STAFF.value,
                created_by=admin_session.admin_id,
            )
        )
        await session.commit()
    except SQLAlchemyError:
        raise HTTPException(
            500, "Failed to create a new admin, this is not from an erroneous input"
        )

    return JSONResponse({"message": "Admin created"}, 202)
