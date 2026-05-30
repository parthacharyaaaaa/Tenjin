from pathlib import Path
import ecdsa
import orjson
from redis import Redis
from datetime import datetime
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from sqlalchemy import select, update, insert, func
from sqlalchemy.ext.asyncio.session import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from auth_server.config.app_config import AppConfig
from auth_server.dependencies import (
    get_app_config,
    get_keydata_repository,
    get_synced_store_client,
    get_database_session,
    get_token_manager,
)
from auxillary.utils import (
    json_repr,
    to_base64url,
)
from auth_server.repositories.keydata import KeydataRepository
from auth_server.security.token_manager import TokenManager
from auth_server.utils.auth_auxillary import report_suspicious_activity
from auth_server.utils.decorators import admin_only
from auth_server.models.database import KeyData
from auth_server.security.keygen import (
    generate_ecdsa_pair,
    write_ecdsa_pair,
    update_jwks,
)
from auth_server.security.key_container import KeyMetadata

KEY: Final[APIRouter] = APIRouter()


@KEY.get("/keys/{kid}")
@admin_only()
async def get_key(
    kid: str, session: Annotated[AsyncSession, Depends(get_database_session)]
) -> JSONResponse:
    try:
        key: KeyData | None = (
            await session.execute(select(KeyData).where(KeyData.kid == kid))
        ).scalar_one_or_none()

        if not key:
            raise HTTPException(404, "No key with this ID found")
    except SQLAlchemyError:
        raise Exception

    key_mapping: dict[str, Any] = json_repr(key)
    # KeyData.__json_like__ does not expose private PEM
    key_mapping["private_pem"] = key.private_pem.decode()

    return JSONResponse(key_mapping)


@KEY.delete("/keys/{kid}")
@admin_only(required_role="super")
async def invalidate_key(
    kid: str,
    config: Annotated[AppConfig, Depends(get_app_config)],
    token_manager: Annotated[TokenManager, Depends(get_token_manager)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    keydata_repository: Annotated[KeydataRepository, Depends(get_keydata_repository)],
    synced_store_client: Annotated[Redis, Depends(get_synced_store_client)],
) -> JSONResponse:
    """Invalidate a given key"""
    key_lock: Final[str] = f"INVALIDATE_KEY:{kid}"
    if not synced_store_client.set(
        key_lock, g.SESSION_TOKEN["admin_id"], ex=300, nx=True
    ):
        # Another worker is performing clean operation, reject this request
        adminID: bytes = synced_store_client.get(key_lock)  # type: ignore[reportAssignmentType]
        return JSONResponse(
            {
                "message": "There is an active keystore clean being performed, your request has been rejected",
                "admin_id": adminID.decode(),
            },
            status_code=409,
        )

    public_pem_fpath: Path = config.JWKS.PUBLIC_PEM_DIRECTORY / f"public_{kid}_key.pem"
    additional_kw: dict[str, str] = {}
    original_jwks: list[dict[str, Any]] = []

    with open(config.JWKS.JWKS_FILEPATH, "r") as jwks_file:
        original_jwks = orjson.loads(jwks_file.read())["keys"]

    if any(mapping["kid"] == kid for mapping in original_jwks):
        additional_kw["jwks_integrity_warning"] = "This key ID was not found in JWKS"

    target_key: KeyData | None = None
    try:
        # Select and lock key if exists
        target_key = (
            await session.execute(
                select(KeyData).where(KeyData.kid == kid).with_for_update(nowait=True)
            )
        ).scalar_one_or_none()

        # Key exists
        if not target_key:
            raise HTTPException(404, f"No key with ID {kid} found")

        # Key is inactive
        if not target_key.rotated_out_at:
            # Key is active, cannot expire directly
            await report_suspicious_activity(
                session,
                config,
                synced_store_client,
                g.SESSION_TOKEN["admin_id"],
                f"Invaldiation attempt on active key {kid}",
            )
            raise HTTPException(
                409, f"Active key {kid} must be rotated out before being invalidated"
            )

        # Key is still valid for verification
        if target_key.expired_at:
            raise HTTPException(409, f"Key {kid} has already been expired")

        await session.execute(
            update(KeyData).where(KeyData.kid == kid).values(expired_at=datetime.now())
        )

        # Before persisting to DB, delete public PEM file, and update JWKS
        updated_jwks = [mapping for mapping in original_jwks if mapping["kid"] != kid]
        with open(config.JWKS.JWKS_FILEPATH, "wb") as jwks_file:
            jwks_file.write(
                orjson.dumps({"keys": updated_jwks}, option=orjson.OPT_INDENT_2)
            )
        # Delete public PEM file
        public_pem_fpath.unlink(missing_ok=True)

        # File I/O done, commit DB
        await session.commit()
    except (SQLAlchemyError, OSError) as exc:
        # Rollback DB
        await session.rollback()

        # Revert JWKS state
        with open(config.JWKS.JWKS_FILEPATH, "wb") as jwks_file:
            jwks_file.write(
                orjson.dumps({"keys": original_jwks}, option=orjson.OPT_INDENT_2)
            )

        # Regenerate PEM file
        if target_key and not public_pem_fpath.exists():
            public_pem_fpath.write_bytes(target_key.public_pem)

        # State reverted, crash and burn
        error: HTTPException = HTTPException(500, f"Failed to invalidate key {kid}")
        setattr(error, "additional_kwargs", additional_kw)
        raise error from exc

    # Key invalidation successful, update local token manager
    token_manager.invalidate_key(kid)

    # Update global
    raw_valid_keys: list[bytes] = synced_store_client.lrange("VALID_KEYS", 0, -1)  # type: ignore[reportAssignmentType]
    if not raw_valid_keys or kid.encode("utf-8") not in raw_valid_keys:
        # Should never happen, but in case it does we fall back and regenerate the entire list
        additional_kw["keylist_integrity_warning"] = (
            "Synced keylist state was inconsistent and hence regenerated through database"
        )
        valid_keys: list[str] = [
            k.kid for k in await keydata_repository.get_relevant_keydata(None)
        ]
    else:
        valid_keys: list[str] = [key.decode() for key in raw_valid_keys]
        valid_keys.remove(kid)

    # By this stage, valid_keys will maintain a consistent sequence of
    # valid key IDs (including that of the active key),
    # either from a simple list removal
    # or by consulting the database in case of any inconsistency

    with synced_store_client.pipeline() as pipe:
        pipe.delete("VALID_KEYS")
        pipe.lpush("VALID_KEYS", *valid_keys)
        pipe.delete(key_lock)
        pipe.execute()

    return JSONResponse(
        {
            "message": "Key invalidated successfully",
            "purged_kid": kid,
            "valid_keys": valid_keys,
            **additional_kw,
        }
    )


@KEY.delete("/keys/clean")
@admin_only(required_role="super")
async def clean_keystore(
    config: Annotated[AppConfig, Depends(get_app_config)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    synced_store_client: Annotated[Redis, Depends(get_synced_store_client)],
) -> JSONResponse:
    """Invalidate all keys except for the currently active key"""
    # Check whether another worker is performing this action
    if not synced_store_client.set(
        "CLEAN_KEYSTORE_LOCK", g.SESSION_TOKEN["admin_id"], ex=300, nx=True
    ):
        # Another worker is performing clean operation, reject this request
        adminID: bytes = synced_store_client.get("CLEAN_KEYSTORE_LOCK")  # type: ignore[reportAssignmentType]
        return JSONResponse(
            {
                "message": "There is an active keystore clean being performed, your request has been rejected",
                "admin_id": adminID.decode(),
            },
            status_code=409,
        )

    # Before cleaning keystore, store all old data for rollbacks
    old_jwks: list[dict[str, Any]] = []
    with open(config.JWKS.JWKS_FILEPATH) as jwks_file:
        old_jwks = orjson.loads(jwks_file.read())["keys"]

    if len(old_jwks) == 1:
        raise HTTPException(409, "No inactive keys present to invalidate")

    pem_mappings: dict[str, bytes] = {}
    for keydata in old_jwks:
        pem_mappings[keydata["kid"]] = config.JWKS.PUBLIC_PEM_DIRECTORY.joinpath(
            f'public_{keydata["kid"]}_key.pem'
        ).read_bytes()

    # At this stage, we have all the old data saved for a rollback.
    # JWKS can be restored, and any PEM files deleted in an erroneous transaction
    # can be regenerated safely
    try:
        # Fetch and lock all keys that have been rotated out, but not expired
        validInactiveKeys: list[str] = list(
            (
                await session.execute(
                    select(KeyData.kid)
                    .where(
                        (KeyData.expired_at == None)
                        & (KeyData.rotated_out_at.isnot(None))
                    )
                    .with_for_update(key_share=True)
                )
            )
            .scalars()
            .all()
        )

        # Update and set as invalid, hence these keys can no longer be used for verification either
        await session.execute(
            update(KeyData)
            .where(KeyData.kid.in_(validInactiveKeys))
            .values(expired_at=datetime.now())
        )

        # Fetch latest KID to prune JWKS and PEM files accordingly
        active_key: KeyData = (
            await session.execute(
                select(KeyData).where(KeyData.rotated_out_at.is_(None))
            )
        ).scalar_one()

        verification_key: ecdsa.VerifyingKey = ecdsa.VerifyingKey.from_pem(
            active_key.public_pem.decode()
        )
        # ecdsa.VerifyingKey.pubkey is hinted as being None,
        # thanks to its constructor,
        # but actually does return a valid type
        active_key_mapping: dict[str, Any] = {
            "kty": "EC",
            "alg": "ECDSA",
            "crv": str(ecdsa.SECP256k1),
            "use": "sig",
            "kid": active_key.kid,
            "x": to_base64url(int(verification_key.pubkey.point.x())),  # type: ignore[reportAttributeAccessIssue]
            "y": to_base64url(int(verification_key.pubkey.point.y())),  # type: ignore[reportAttributeAccessIssue]
        }

        with open(config.JWKS.JWKS_FILEPATH, "wb") as jwks_file:
            jwks_file.write(
                orjson.dumps({"keys": [active_key_mapping]}, option=orjson.OPT_INDENT_2)
            )

        # Purge all public PEM files for invalid keys
        for keyID in validInactiveKeys:
            (
                config.JWKS.PUBLIC_PEM_DIRECTORY.joinpath(
                    f"public_{keyID}_key.pem"
                ).unlink(missing_ok=True)
            )

        await session.commit()  # Finally persist this transaction at the most important layer i.e. DB
    except Exception as exc:
        # Perform full rollback
        # DB
        await session.rollback()

        # JWKS
        with open(config.JWKS.JWKS_FILEPATH, "wb") as jwks_file:
            jwks_file.write(
                orjson.dumps({"keys": old_jwks}, option=orjson.OPT_INDENT_2)
            )

        # PEM files
        for kid, public_pem in pem_mappings.items():
            fpath: Path = config.JWKS.PUBLIC_PEM_DIRECTORY / f"public_{kid}_key.pem"
            # Regenerate public PEM file in case of deletion
            if not fpath.exists():
                fpath.write_bytes(public_pem)

        # All rollbacks performed, crash and burn
        raise HTTPException(500, "Failed to perform clean operation") from exc
    finally:
        synced_store_client.delete("CLEAN_KEYSTORE_LOCK")

    # Update global state, no need to fetch current list of keys anyways since as of this operation only a single active key would be valid throughout
    with synced_store_client.pipeline() as pipe:
        pipe.delete("VALID_KEYS")
        pipe.lpush("VALID_KEYS", active_key.kid)
        pipe.execute()

    return JSONResponse(
        {
            "message": "All inactive keys have been invalidated",
            "invalidated keys": validInactiveKeys,
            "active_key": active_key.kid,
        }
    )


@KEY.post("/keys/rotate")
@admin_only()
async def rotate_keys(
    config: Annotated[AppConfig, Depends(get_app_config)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    keydata_repository: Annotated[KeydataRepository, Depends(get_keydata_repository)],
    synced_store_client: Annotated[Redis, Depends(get_synced_store_client)],
    token_manager: Annotated[TokenManager, Depends(get_token_manager)],
) -> JSONResponse:
    """Trigger a key rotation sequence"""
    # Check for concurrent worker performing a key rotation
    lock = synced_store_client.set(
        "KEY_ROTATION_LOCK", g.SESSION_TOKEN["admin_id"], ex=300, nx=True
    )
    if not lock:
        # Another worker is performing this action, reject this request >:(
        adminID: bytes = synced_store_client.get("KEY_ROTATION_LOCK")  # type: ignore[reportAssignmentType]
        return JSONResponse(
            {
                "message": "There is an active key rotation being performed, your request has been rejected",
                "admin_id": adminID.decode(),
            },
            status_code=409,
        )

    # Check for cooldown, must be global for all staff admins
    cooldown_flag: str = synced_store_client.get("KEY_ROTATION_COOLDOWN")  # type: ignore[reportAssignmentType]
    if cooldown_flag and g.SESSION_TOKEN["role"] == "staff":
        await report_suspicious_activity(
            session,
            config,
            synced_store_client,
            g.SESSION_TOKEN["admin_id"],
            "Attempt to perform key rotation during cooldown",
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

    # Update DB first, then perform JWKS and PEM writes
    overflow: bool = False
    id_: str | None = None
    try:
        # Update currently active key
        previous_key_id: str = (
            await session.execute(
                select(KeyData.kid)
                .where(KeyData.rotated_out_at == None)
                .with_for_update(nowait=True, key_share=True)
            )
        ).scalar_one()

        # Reflect rotation in DB
        await session.execute(
            update(KeyData)
            .where(KeyData.kid == previous_key_id)
            .values(
                rotated_out_at=datetime.now(),
                manual_rotation=True,
                rotated_by=g.SESSION_TOKEN["admin_id"],
            )
        )

        # Add new key
        await session.execute(
            insert(KeyData).values(
                kid=kid,
                curve=str(ecdsa.SECP256k1),
                private_pem=signing_key.to_pem(),
                public_pem=verification_key.to_pem(),
            )
        )

        # Check whether max capacity has been reached. If so, purge oldest key
        valid_key_count: int = (
            await session.execute(
                select(func.count())
                .select_from(KeyData)
                .where(KeyData.expired_at == None)
            )
        ).scalar_one()

        if valid_key_count > config.KEYS.MAX_VALID_KEYS:
            overflow = True

            # Select and lock oldest, non-expired valid key
            id_ = (
                await session.execute(
                    select(KeyData.kid)
                    .where(
                        (KeyData.rotated_out_at.isnot(None))
                        & (KeyData.expired_at.is_(None))
                    )
                    .with_for_update(nowait=True)
                    .order_by(KeyData.rotated_out_at.asc())
                    .limit(1)
                )
            ).scalar_one()

            # Update expired_at column
            await session.execute(
                update(KeyData)
                .where(KeyData.kid == id_)
                .values(expired_at=datetime.now())
            )
        await session.commit()

    except SQLAlchemyError:
        await session.rollback()
        synced_store_client.delete("KEY_ROTATION_LOCK")
        raise HTTPException(
            500, "An error occured in performing key rotation (Database level)"
        )

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
    config.JWKS.JWKS_FILEPATH.joinpath(f"private_{previous_key_id}_key.pem").unlink()
    if overflow:
        # Delete oldest public PEM file.
        config.JWKS.JWKS_FILEPATH.joinpath(f"public_{id_}_key.pem").unlink()
        config.JWKS.JWKS_FILEPATH.joinpath(f"private_{id_}_key.pem").unlink(
            missing_ok=True
        )

    # Update token manager's mapping to use this newly created ECDSA pair
    newKeyData: KeyMetadata = KeyMetadata(
        PUBLIC_PEM=verification_key.to_pem(),
        PRIVATE_PEM=signing_key.to_pem(),
        ALGORITHM="ES256",
    )
    token_manager.update_keydata(kid, newKeyData)

    raw_valid_keys: list[bytes] = synced_store_client.lrange("VALID_KEYS", 0, -1)  # type: ignore[reportAssignmentType]
    if not raw_valid_keys or kid.encode("utf-8") not in raw_valid_keys:
        # Should never happen, but in case it does we fall back and regenerate the entire list
        valid_keys: list[str] = [
            k.kid for k in await keydata_repository.get_relevant_keydata(None)
        ]
    else:
        valid_keys: list[str] = [key.decode() for key in raw_valid_keys]

    if overflow and id_ in valid_keys:
        # Remove invalidated key ID
        valid_keys.remove(id_)

    # At this state, valid_keys is a consistent list of key IDs
    # Set global cooldown for key rotation, update global state, and release rotation lock
    with synced_store_client.pipeline() as pipe:
        pipe.set("KEY_ROTATION_COOLDOWN", 1, ex=config.KEYS.KEY_ROTATION_COOLDOWN)
        pipe.delete("VALID_KEYS")
        pipe.lpush("VALID_KEYS", *valid_keys)
        pipe.delete("KEY_ROTATION_LOCK")
        pipe.execute()

    return JSONResponse(
        {
            "message": "Key rotation successful",
            "kid": kid,
            "public_pem": newKeyData.PUBLIC_PEM.decode(),
            "epoch": newKeyData.EPOCH,
            "alg": "ES256",
            "previous_kid": previous_key_id,
        },
        status_code=201,
    )
