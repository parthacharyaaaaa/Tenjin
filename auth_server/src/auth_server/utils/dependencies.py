import base64
import time
from typing import Annotated, Any, Final

import orjson
import pydantic
from auxillary.data_structures.uow import MultiRepositoryWorkCoordinator
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
from cryptography.hazmat.primitives.asymmetric.types import PublicKeyTypes
from fastapi import Depends, HTTPException, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from auth_server.config.app_config import AppConfig
from auth_server.dependencies import (
    get_admin_repository,
    get_app_config,
    get_repository_work_coordinator,
    get_suspicious_activity_repository,
    get_synced_store_client,
)
from auth_server.models.session import AdminSession
from auth_server.repositories.admin import AdminPublicResult, AdminRepository
from auth_server.repositories.suspicious_activity import SuspiciousActivityRepository
from auth_server.security.admin_roles import ROLE_PERMISSIONS, AdminRole
from auth_server.security.permissions import Permission
from auth_server.strings import AdminStrings
from auth_server.utils.auth_auxillary import report_suspicious_activity
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
    synced_store_client: Annotated[Redis, Depends(get_synced_store_client)],
    admin_context: Annotated[AdminContext, Depends(get_admin_session)],
    admin_repository: Annotated[AdminRepository, Depends(get_admin_repository)],
) -> ec.EllipticCurvePublicKey:
    try:
        key_pem: bytes | None = await synced_store_client.hget(  # pyrefly: ignore
            AdminStrings.ADMIN_KEY_CACHE, str(admin_context.session.admin_id)
        )

        if key_pem:
            key: PublicKeyTypes = serialization.load_pem_public_key(key_pem)
            if not isinstance(key, EllipticCurvePublicKey):
                await synced_store_client.hdel(  # pyrefly: ignore[not-async]
                    AdminStrings.ADMIN_KEY_CACHE, str(admin_context.session.admin_id)
                )
                key_pem = None
            else:
                return key
    except RedisError:
        # TODO: Some logging
        pass
    try:
        # A None check would be redundant here, admin existence is known
        admin: AdminPublicResult | None = await admin_repository.get_admin(
            admin_context.session.admin_id
        )
        if not admin:  # should never happen
            await synced_store_client.hdel(  # pyrefly: ignore[not-async]
                AdminStrings.ADMIN_KEY_CACHE, str(admin_context.session.admin_id)
            )
            raise HTTPException(401, "Invalid session")

        key: PublicKeyTypes = serialization.load_pem_public_key(admin.verification_key)
        if not isinstance(key, EllipticCurvePublicKey):
            e = ValueError("Serialized key is not EC")
            e.add_note(rf"key: {admin.verification_key}")
            raise e
        return key
    except SQLAlchemyError as e:
        raise HTTPException(500) from e


async def validate_admin_session(
    config: Annotated[AppConfig, Depends(get_app_config)],
    admin_context: Annotated[AdminContext, Depends(get_admin_session)],
    verification_key: Annotated[
        ec.EllipticCurvePublicKey, Depends(get_verification_key)
    ],
    synced_store_client: Annotated[Redis, Depends(get_synced_store_client)],
    admin_repository: Annotated[AdminRepository, Depends(get_admin_repository)],
    suspicious_activity_repository: Annotated[
        SuspiciousActivityRepository, Depends(get_suspicious_activity_repository)
    ],
    coordinator: Annotated[
        MultiRepositoryWorkCoordinator, Depends(get_repository_work_coordinator)
    ],
) -> AdminSession:

    bare_token, signature = admin_context.session_token.split(".")
    try:
        verification_key.verify(
            signature.encode("utf-8"),
            bare_token.encode("utf-8"),
            ec.ECDSA(hashes.SHA256()),
        )
    except InvalidSignature:
        err_msg: str = "Tampered/invalid session"
        await report_suspicious_activity(
            config,
            synced_store_client,
            admin_context.session.admin_id,
            err_msg,
            suspicious_activity_repository,
            admin_repository,
            coordinator,
        )
        raise HTTPException(401, err_msg)

    if time.time() >= admin_context.session.expiry_at:
        await synced_store_client.delete(admin_context.session.session_key)
        raise HTTPException(401, "Session expired")

    server_session_mapping: dict[bytes, Any] = await synced_store_client.hgetall(  # pyrefly: ignore[not-async]
        admin_context.session.session_key
    )
    if not server_session_mapping:
        err_msg: str = "Missing server-side session"
        await report_suspicious_activity(
            config,
            synced_store_client,
            admin_context.session.admin_id,
            err_msg,
            suspicious_activity_repository,
            admin_repository,
            coordinator,
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
