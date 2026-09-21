import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Final

import aiofiles
import orjson
from auxillary.data_structures.uow import MultiRepositoryWorkCoordinator
from auxillary.utils import (
    json_repr,
    to_base64url,
)
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.types import PublicKeyTypes
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_public_key,
)
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.exc import SQLAlchemyError

from auth_server.config.app_config import AppConfig
from auth_server.dependencies import (
    get_admin_repository,
    get_admin_session_manager,
    get_app_config,
    get_key_lifecycle_manager,
    get_keydata_repository,
    get_repository_work_coordinator,
    get_suspicious_activity_repository,
    get_synced_store_client,
    get_synced_store_key_state_manager,
    get_token_manager,
)
from auth_server.models.session import AdminSession
from auth_server.repositories.admin import AdminRepository
from auth_server.repositories.keydata import (
    KeydataRepository,
    KeyPrivateDataResult,
    KeyPublicDataResult,
)
from auth_server.repositories.suspicious_activity import SuspiciousActivityRepository
from auth_server.security.admin_roles import AdminRole
from auth_server.security.admin_sessions import AdminSessionManager
from auth_server.security.key_container import KeyMetadata
from auth_server.security.key_manager import (
    KeyLifecycleManager,
    SyncedStoreKeyStateManager,
)
from auth_server.security.keygen import (
    generate_ecdsa_pair,
    update_jwks,
    write_ecdsa_pair,
)
from auth_server.security.permissions import Permission
from auth_server.security.token_manager import TokenManager
from auth_server.strings import SelectionLockOption, SyncedStoreStrings
from auth_server.utils.auth_auxillary import report_suspicious_activity
from auth_server.utils.dependencies import require_permissions

KEY: Final[APIRouter] = APIRouter()


@KEY.get("/keys/{kid}")
async def get_key(
    kid: str,
    admin_session: Annotated[
        AdminSession, Depends(require_permissions(Permission.READ_KEY))
    ],
    keydata_repository: Annotated[KeydataRepository, Depends(get_keydata_repository)],
    public: bool = True,
) -> JSONResponse:
    try:
        key: (
            KeyPublicDataResult | KeyPrivateDataResult | None
        ) = await keydata_repository.get_keydata(kid, public_only=public)

        if not key:
            raise HTTPException(404, "No key with this ID found")
    except SQLAlchemyError:
        raise Exception("Failed to fetch key")

    return JSONResponse(json_repr(key))


@KEY.delete("/keys/{kid}")
async def invalidate_key(
    kid: str,
    admin_session: Annotated[
        AdminSession, Depends(require_permissions(Permission.INVALIDATE_KEY))
    ],
    key_lifecycle_manager: Annotated[
        KeyLifecycleManager, Depends(get_key_lifecycle_manager)
    ],
    synced_keystate_manager: Annotated[
        SyncedStoreKeyStateManager, Depends(get_synced_store_key_state_manager)
    ],
) -> JSONResponse:
    additional_kw: dict[str, str] = {}
    await key_lifecycle_manager.invalidate_key(
        kid, intermediate_message_mapping=additional_kw
    )

    valid_keys: list[str] = await synced_keystate_manager.get_valid_keys_ids()
    return JSONResponse(
        {
            "message": "Key invalidated successfully",
            "purged_kid": kid,
            "valid_keys": valid_keys,
            "additional_info": additional_kw,
        }
    )


@KEY.delete("/keys/clean")
async def clean_keystore(
    admin_session: Annotated[
        AdminSession, Depends(require_permissions(Permission.CLEAN_KEYSTORE))
    ],
    config: Annotated[AppConfig, Depends(get_app_config)],
    keydata_repository: Annotated[KeydataRepository, Depends(get_keydata_repository)],
    synced_store_client: Annotated[Redis, Depends(get_synced_store_client)],
) -> JSONResponse:
    """Invalidate all keys except for the currently active key"""
    # Check whether another worker is performing this action
    if not await synced_store_client.set(
        "CLEAN_KEYSTORE_LOCK", admin_session.admin_id, ex=300, nx=True
    ):
        # Another worker is performing clean operation, reject this request
        admin_id: bytes = await synced_store_client.get("CLEAN_KEYSTORE_LOCK")  # type: ignore[reportAssignmentType]
        return JSONResponse(
            {
                "message": "There is an active keystore clean being performed, your request has been rejected",
                "admin_id": admin_id.decode(),
            },
            status_code=409,
        )

    # Before cleaning keystore, store all old data for rollbacks
    old_jwks: list[dict[str, Any]] = []
    async with aiofiles.open(config.JWKS.JWKS_FILEPATH) as jwks_file:
        old_jwks = orjson.loads(await jwks_file.read())["keys"]

    if len(old_jwks) == 1:
        raise HTTPException(409, "No inactive keys present to invalidate")

    pem_mappings: dict[str, bytes] = {}
    for keydata in old_jwks:
        pem_mappings[keydata["kid"]] = config.JWKS.PUBLIC_PEM_DIRECTORY.joinpath(
            f"public_{keydata['kid']}_key.pem"
        ).read_bytes()

    # At this stage, we have all the old data saved for a rollback.
    # JWKS can be restored, and any PEM files deleted in an erroneous transaction
    # can be regenerated safely
    async with keydata_repository.unit_of_work():
        try:
            # Fetch and lock all keys that have been rotated out, but not expired
            valid_inactive_keys: list[
                KeyPublicDataResult
            ] = await keydata_repository.get_valid_inactive_keys(
                lock_args=(SelectionLockOption.KEY_SHARE, SelectionLockOption.READ)
            )

            # Update and set as invalid, hence these keys can no longer be used for verification either
            await keydata_repository.batch_expire_keydata(
                tuple(k.kid for k in valid_inactive_keys)
            )

            # Fetch latest KID to prune JWKS and PEM files accordingly
            active_key: (
                KeyPublicDataResult | None
            ) = await keydata_repository.get_active_key()
            if not active_key:  # Violates business invariant, should never happen
                raise HTTPException(500, "Invalid keystore state!")

            verification_key: PublicKeyTypes = load_pem_public_key(
                active_key.public_pem
            )

            # Should never happen
            if not isinstance(verification_key, ec.EllipticCurvePublicKey):
                raise HTTPException(500, "Invalid active key")
            public_numbers: ec.EllipticCurvePublicNumbers = (
                verification_key.public_numbers()
            )
            active_key_mapping: dict[str, Any] = {
                "kty": "EC",
                "alg": "ECDSA",
                "crv": ec.SECP256K1.name,
                "use": "sig",
                "kid": active_key.kid,
                "x": to_base64url(public_numbers.x),
                "y": to_base64url(public_numbers.y),
            }

            async with aiofiles.open(config.JWKS.JWKS_FILEPATH, "wb") as jwks_file:
                await jwks_file.write(
                    orjson.dumps(
                        {"keys": [active_key_mapping]}, option=orjson.OPT_INDENT_2
                    )
                )

            # Purge all public PEM files for invalid keys
            for key_id in valid_inactive_keys:
                (
                    config.JWKS.PUBLIC_PEM_DIRECTORY.joinpath(
                        f"public_{key_id}_key.pem"
                    ).unlink(missing_ok=True)
                )
        except Exception as exc:
            # JWKS
            async with aiofiles.open(config.JWKS.JWKS_FILEPATH, "wb") as jwks_file:
                await jwks_file.write(
                    orjson.dumps({"keys": old_jwks}, option=orjson.OPT_INDENT_2)
                )

            # PEM files
            for kid, public_pem in pem_mappings.items():
                fpath: Path = config.JWKS.PUBLIC_PEM_DIRECTORY / f"public_{kid}_key.pem"
                # Regenerate public PEM file in case of deletion
                if not await asyncio.to_thread(fpath.exists):
                    await asyncio.to_thread(fpath.write_bytes, public_pem)

            # All rollbacks performed, crash and burn
            raise HTTPException(500, "Failed to perform clean operation") from exc
        finally:
            synced_store_client.delete("CLEAN_KEYSTORE_LOCK")

    # Update global state, no need to fetch current list of keys
    # anyways since as of this operation only a single active key would be valid throughout
    async with synced_store_client.pipeline() as pipe:
        pipe.delete(SyncedStoreStrings.VALID_KEYS)
        pipe.lpush(SyncedStoreStrings.VALID_KEYS, active_key.kid)
        await pipe.execute()

    return JSONResponse(
        {
            "message": "All inactive keys have been invalidated",
            "invalidated keys": valid_inactive_keys,
            "active_key": active_key.kid,
        }
    )


@KEY.post("/keys/rotate")
async def rotate_keys(
    admin_session: Annotated[
        AdminSession, Depends(require_permissions(Permission.ROTATE_KEY))
    ],
    config: Annotated[AppConfig, Depends(get_app_config)],
    keydata_repository: Annotated[KeydataRepository, Depends(get_keydata_repository)],
    synced_store_client: Annotated[Redis, Depends(get_synced_store_client)],
    token_manager: Annotated[TokenManager, Depends(get_token_manager)],
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
    """Trigger a key rotation sequence"""
    # Check for concurrent worker performing a key rotation
    lock = await synced_store_client.set(
        SyncedStoreStrings.KEY_ROTATION_LOCK, admin_session.admin_id, ex=300, nx=True
    )
    if not lock:
        # Another worker is performing this action, reject this request >:(
        admin_id: bytes = await synced_store_client.get(
            SyncedStoreStrings.KEY_ROTATION_LOCK
        )  # type: ignore[reportAssignmentType]
        return JSONResponse(
            {
                "message": "There is an active key rotation being performed, your request has been rejected",
                "admin_id": admin_id.decode(),
            },
            status_code=409,
        )

    # Check for cooldown, must be global for all staff admins
    cooldown_flag: str = await synced_store_client.get(
        SyncedStoreStrings.KEY_ROTATION_COOLDOWN
    )  # type: ignore[reportAssignmentType]
    if cooldown_flag and admin_session.role == AdminRole.STAFF:
        await report_suspicious_activity(
            config,
            admin_session.admin_id,
            "Attempt to perform key rotation during cooldown",
            suspicious_activity_repository,
            admin_repository,
            repository_coordinator,
            admin_session_manager,
        )
        raise HTTPException(
            409,
            " ".join(
                (
                    "The server is currently undergoing a key rotation cooldown,",
                    "and will not accept rotation requests.",
                    "Repeated attempt will lead to account lock",
                )
            ),
        )

    # Server is ready for a key rotation
    kid, signing_key, verification_key = generate_ecdsa_pair()
    generation_epoch: Final[datetime] = datetime.now(UTC)
    private_pem: Final[bytes] = signing_key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption(),
    )
    public_pem: Final[bytes] = verification_key.public_bytes(
        encoding=Encoding.PEM,
        format=PublicFormat.SubjectPublicKeyInfo,
    )

    # Update DB first, then perform JWKS and PEM writes
    overflow: bool = False
    target_id: str | None = None
    async with keydata_repository.unit_of_work():
        try:
            # Update currently active key
            previous_key: (
                KeyPublicDataResult | None
            ) = await keydata_repository.get_active_key()
            if not previous_key:
                raise HTTPException(500, "Invalid key state!")

            # Reflect rotation in DB
            await keydata_repository.rotate_key(
                previous_key.kid,
                kid,
                new_key_public_pem=public_pem,
                new_key_private_pem=private_pem,
                rotation_author=admin_session.admin_id,
                epoch=generation_epoch,
            )

            # Check whether max capacity has been reached. If so, purge oldest key
            valid_inactive_key_data: list[tuple[str, datetime]] = [
                (i.kid, i.rotated_out_at)
                for i in (
                    await keydata_repository.get_valid_inactive_keys(
                        lock_args=(
                            SelectionLockOption.READ,
                            SelectionLockOption.KEY_SHARE,
                        )
                    )
                )
            ]

            if len(valid_inactive_key_data) > config.KEYS.MAX_VALID_KEYS:
                overflow = True

                target_id = sorted(valid_inactive_key_data, key=lambda x: x[1])[0][0]
                await keydata_repository.expire_keydata(target_id)
        except SQLAlchemyError:
            await synced_store_client.delete(SyncedStoreStrings.KEY_ROTATION_LOCK)
            raise HTTPException(500, "An error occured in performing key rotation")

    # Update files
    update_jwks(
        verification_key,
        kid,
        config.JWKS.JWKS_FILEPATH,
        capacity=config.KEYS.MAX_VALID_KEYS,
    )

    write_ecdsa_pair(
        private_dir=config.JWKS.PRIVATE_PEM_DIRECTORY,
        public_dir=config.JWKS.PUBLIC_PEM_DIRECTORY,
        private_key=signing_key,
        public_key=verification_key,
        key_id=kid,
    )

    # Remove previous key's private PEM file
    config.JWKS.JWKS_FILEPATH.joinpath(f"private_{previous_key.kid}_key.pem").unlink()
    if overflow:
        # Delete oldest public PEM file.
        config.JWKS.JWKS_FILEPATH.joinpath(f"public_{target_id}_key.pem").unlink()
        config.JWKS.JWKS_FILEPATH.joinpath(f"private_{target_id}_key.pem").unlink(
            missing_ok=True
        )

    # Update token manager's mapping to use this newly created ECDSA pair
    new_keydata: KeyMetadata = KeyMetadata(
        PUBLIC_PEM=public_pem,
        PRIVATE_PEM=private_pem,
        ALGORITHM="ES256",
    )
    token_manager.update_keydata(kid, new_keydata)

    raw_valid_keys: list[bytes] = synced_store_client.lrange("VALID_KEYS", 0, -1)  # type: ignore[reportAssignmentType]
    if not raw_valid_keys or kid.encode("utf-8") not in raw_valid_keys:
        # Should never happen, but in case it does we fall back and regenerate the entire list
        valid_keys: list[str] = [
            k.kid for k in await keydata_repository.get_relevant_keydata()
        ]
    else:
        valid_keys: list[str] = [key.decode() for key in raw_valid_keys]

    if overflow and target_id in valid_keys:
        # Remove invalidated key ID
        # in this branch, target_id will always be str since valid_keys is always Sequence[str]
        valid_keys.remove(target_id)  # pyrefly: ignore[bad-argument-type]

    # At this state, valid_keys is a consistent list of key IDs
    # Set global cooldown for key rotation, update global state, and release rotation lock
    async with synced_store_client.pipeline() as pipe:
        pipe.set(
            SyncedStoreStrings.KEY_ROTATION_COOLDOWN,
            1,
            ex=config.KEYS.KEY_ROTATION_COOLDOWN,
        )
        pipe.delete(SyncedStoreStrings.VALID_KEYS)
        pipe.lpush(SyncedStoreStrings.VALID_KEYS, *valid_keys)
        pipe.delete(SyncedStoreStrings.KEY_ROTATION_LOCK)
        await pipe.execute()

    return JSONResponse(
        {
            "message": "Key rotation successful",
            "kid": kid,
            "public_pem": new_keydata.PUBLIC_PEM.decode(),
            "epoch": new_keydata.EPOCH,
            "alg": "ES256",
            "previous_kid": previous_key.kid,
        },
        status_code=201,
    )
