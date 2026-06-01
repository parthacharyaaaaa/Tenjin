import base64
import time
from typing import Annotated, Any, Final

import ecdsa

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from auth_server.config.app_config import AppConfig
from auth_server.security.admin_roles import ROLE_PERMISSIONS, AdminRole
from auth_server.security.permissions import Permission
from sqlalchemy.ext.asyncio import AsyncSession

from auth_server.models.session import AdminSession
from auth_server.models.database import Admin
from auth_server.utils.auth_auxillary import report_suspicious_activity
from fastapi import Depends, HTTPException, Request

import orjson

import pydantic

from redis.exceptions import RedisError
from redis.asyncio import Redis

from auth_server.dependencies import (
    get_app_config,
    get_database_session,
    get_synced_store_client,
)
from auth_server.strings import AdminStrings

from auth_server.utils.datastructures import AdminContext


async def get_admin_session(request: Request) -> AdminContext:
    session_token: Final[str | None] = request.headers.get(
        AdminStrings.SESSION_TOKEN_HEADER
    )
    if not session_token:
        raise HTTPException(
            401, f"Missing session token: {AdminStrings.SESSION_TOKEN_HEADER}"
        )

    try:
        session: Final[AdminSession] = AdminSession.model_validate(
            orjson.loads(base64.urlsafe_b64decode(session_token))
        )
    except (UnicodeEncodeError, TypeError):
        # urlsafe_b64decode tries to enforce ASCII using encoding,
        # hence raising UnicodeEncodeError even if the overarching operation
        # is decoding
        raise HTTPException(
            401, "Malformed session token, not valid url-safe base-64 encoded"
        )
    except orjson.JSONDecodeError:
        raise HTTPException(401, "Malformed session token, not valid JSON")
    except pydantic.ValidationError as e:
        field_names: list[str | int] = [error["loc"][0] for error in e.errors()]
        raise HTTPException(401, f"Invalid session fields: {field_names}")

    return AdminContext(session_token, session)


async def get_verification_key(
    session: Annotated[AsyncSession, Depends(get_database_session)],
    synced_store_client: Annotated[Redis, Depends(get_synced_store_client)],
    admin_context: Annotated[AdminContext, Depends(get_admin_session)],
) -> ecdsa.VerifyingKey:
    try:
        key_pem: bytes | None = await synced_store_client.hget(
            AdminStrings.ADMIN_KEY_CACHE, admin_context.session.admin_id  # type: ignore
        )

        if key_pem:
            return ecdsa.VerifyingKey.from_pem(key_pem)
    except RedisError:
        # TODO: Some logging
        pass
    try:
        # A None check would be redundant here, admin existence is known
        key_pem = (
            await session.execute(
                select(Admin.verification_key).where(
                    Admin.id_ == admin_context.session.admin_id
                )
            )
        ).scalar_one()

        return ecdsa.VerifyingKey.from_pem(key_pem)
    except SQLAlchemyError as e:
        raise HTTPException(500) from e


async def validate_admin_session(
    config: Annotated[AppConfig, Depends(get_app_config)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    admin_context: Annotated[AdminContext, Depends(get_admin_session)],
    verification_key: Annotated[ecdsa.VerifyingKey, Depends(get_verification_key)],
    synced_store_client: Annotated[Redis, Depends(get_synced_store_client)],
) -> AdminSession:

    signature: Final[str] = admin_context.session_token.split(".")[1]
    try:
        verification_key.verify(
            verification_key, signature, config.ADMIN.SESSION_HASHFUNC
        )
    except ecdsa.BadSignatureError:
        err_msg: str = "Tampered/invalid session"
        await report_suspicious_activity(
            session,
            config,
            synced_store_client,
            admin_context.session.admin_id,
            err_msg,
        )
        raise HTTPException(401, err_msg)

    if time.time() >= admin_context.session.expiry_at:
        await synced_store_client.delete(admin_context.session.session_key)
        raise HTTPException(401, "Session expired")

    server_session_mapping: dict[bytes, Any] = await synced_store_client.hgetall(
        admin_context.session.session_key
    )

    if not server_session_mapping:
        err_msg: str = "Missing server-side session"
        await report_suspicious_activity(
            session,
            config,
            synced_store_client,
            admin_context.session.admin_id,
            err_msg,
            force_logout=False,
        )
        raise HTTPException(401, err_msg)

    server_session_mapping[b"role"] = AdminRole(
        server_session_mapping[b"role"].decode()
    )

    return admin_context.session


def require_permissions(*required_permissions: Permission):
    async def closure(
        admin_session: Annotated[AdminSession, Depends(validate_admin_session)],
    ) -> AdminSession:
        missing: set[Permission] = set(required_permissions) - set(
            ROLE_PERMISSIONS[admin_session.role]
        )
        if missing:
            raise HTTPException(
                403, f"Missing permissions for: {', '.join(m.value for m in missing)}"
            )
        return admin_session

    return closure
