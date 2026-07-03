from contextlib import asynccontextmanager
from datetime import datetime
import os
from pathlib import Path
import time
import traceback
from typing import AsyncGenerator, Final, Mapping, Sequence

from fastapi import APIRouter, FastAPI
from redis.asyncio import Redis

from auxillary.utils import generic_error_handler

from auth_server.routers import ROUTER_URL_MAPPING, RouterName, URLPrefix
from auth_server.config.app_config import AppConfig
from auth_server.dependencies import (
    get_app_config,
    get_database_session_maker,
    get_synced_store_client,
    get_token_manager,
)
from auth_server.security.key_container import KeyMetadata
from auth_server.security.keygen import (
    initialize_active_key,
    write_ecdsa_pair,
    initialize_jwks,
)
from auth_server.models.database import KeyData
from auth_server.repositories.keydata import KeydataRepository
from auth_server.strings import SyncedStoreStrings
from auth_server.security.token_manager import TokenManager


def register_routers(
    app: FastAPI,
    url_prefix_mapping: Mapping[RouterName, tuple[APIRouter, tuple[URLPrefix, ...]]],
    common_prefix: str = "",
) -> None:
    for name, (router, url_prefixes) in url_prefix_mapping.items():
        app.include_router(
            router, prefix="/".join((common_prefix, *[u.value for u in url_prefixes]))
        )


async def _purge_expired_keys(
    public_pem_directory: Path,
    private_pem_directory: Path,
    keydata_repository: KeydataRepository,
) -> None:
    expiredKeys: list[str] = [
        k.kid for k in await keydata_repository.get_expired_keys()
    ]
    for expiredKey in expiredKeys:
        (
            public_pem_directory.joinpath(f"public_{expiredKey}_key.pem").unlink(
                missing_ok=True
            )
        )

        (
            private_pem_directory.joinpath(f"private_{expiredKey}_key.pem").unlink(
                missing_ok=True
            )
        )


def _sync_file_system_key_state(
    active_key: KeyData,
    rotated_keys: Sequence[KeyData],
    public_pem_directory: Path,
    private_pem_directory: Path,
):
    # Sync file state for active key
    write_ecdsa_pair(
        private_pem_directory,
        public_pem_directory,
        active_key.private_pem,
        active_key.public_pem,
        active_key.kid,
    )

    for keyData in rotated_keys:
        private_pem_path: Path = (
            private_pem_directory / f"private_{keyData.kid}_key.pem"
        )
        public_pem_path: Path = public_pem_directory / f"public_{keyData.kid}_key.pem"

        # Ensure that only public pem file exists for verification keys
        public_pem_path.write_bytes(keyData.public_pem)
        private_pem_path.unlink(missing_ok=True)


async def master_bootup(
    config: AppConfig,
    synced_store_client: Redis,
    keydata_repository: KeydataRepository,
    process_id: int,
) -> None:
    print(f"[AUTH {process_id}] Serving as master")

    active_kid: str | None = None
    active_keydata: KeyMetadata | None = None
    rotated_verifying_keys: dict[str, KeyMetadata] | None = None
    failed: bool = False

    try:
        keydata: list[KeyData] = await keydata_repository.get_relevant_keydata(
            limit=None
        )

        if not keydata:
            # No valid keys in DB, master must create new pair
            print(f"[AUTH {process_id}] Creating new key pair")
            active_key: KeyData = await initialize_active_key(
                config.JWKS.PRIVATE_PEM_DIRECTORY,
                config.JWKS.PUBLIC_PEM_DIRECTORY,
                keydata_repository,
            )

            active_keydata = KeyMetadata(
                PUBLIC_PEM=active_key.public_pem,
                PRIVATE_PEM=active_key.private_pem,
                ALGORITHM="ES256",
                EPOCH=datetime.timestamp(active_key.epoch),
            )
            active_kid = active_key.kid

            keydata.append(active_key)

        else:
            verification_only_keys: list[KeyData] = list(
                filter(lambda x: x.rotated_out_at is not None, keydata)
            )
            missing_active: bool = len(keydata) == len(verification_only_keys)

            # Atleast 1 non-expired key exists in DB
            if len(keydata) > config.JWKS.JWKS_CAP:
                keydata_repository.expire_keydata(
                    keydata[config.JWKS.JWKS_CAP - (1 + missing_active)].epoch
                )  # type: ignore
                keydata = keydata[: config.JWKS.JWKS_CAP]

            if missing_active:
                active_key: KeyData = await initialize_active_key(
                    config.JWKS.PRIVATE_PEM_DIRECTORY,
                    config.JWKS.PUBLIC_PEM_DIRECTORY,
                    keydata_repository,
                )
                active_kid = active_key.kid
                active_keydata = KeyMetadata(
                    PUBLIC_PEM=active_key.public_pem,
                    PRIVATE_PEM=active_key.private_pem,
                    ALGORITHM="ES256",
                    EPOCH=datetime.timestamp(active_key.epoch),
                )
                keydata.insert(0, active_key)

            # AND condition is unnecessary here, but helps the type checker
            # confirm that active_keydata != None
            # when calling TokenManager.set_key_state
            if not (active_kid and active_keydata):
                active_kid = keydata[0].kid
                active_keydata = KeyMetadata(
                    PUBLIC_PEM=keydata[0].public_pem,
                    PRIVATE_PEM=keydata[0].private_pem,
                    ALGORITHM="ES256",
                    EPOCH=datetime.timestamp(keydata[0].epoch),
                )

            if len(keydata) > 1:
                rotated_verifying_keys = {
                    k.kid: KeyMetadata(
                        PUBLIC_PEM=k.public_pem,
                        PRIVATE_PEM=k.private_pem,
                        ALGORITHM="ES256",
                        EPOCH=datetime.timestamp(k.epoch),
                    )
                    for k in keydata[1:]
                }

        _sync_file_system_key_state(
            keydata[0],
            keydata[1:],
            config.JWKS.PUBLIC_PEM_DIRECTORY,
            config.JWKS.PRIVATE_PEM_DIRECTORY,
        )

        # Lastly, purge any PEM files for expired keys that are somehow still in file system
        await _purge_expired_keys(
            public_pem_directory=config.JWKS.PUBLIC_PEM_DIRECTORY,
            private_pem_directory=config.JWKS.PRIVATE_PEM_DIRECTORY,
            keydata_repository=keydata_repository,
        )

        initialize_jwks(config.JWKS.JWKS_FILEPATH, keydata)

        # Initialize token manager
        token_manager: Final[TokenManager] = get_token_manager()
        token_manager.set_key_state(active_kid, active_keydata, rotated_verifying_keys)
        print(f"[AUTH {process_id}] Master process bootup complete!")
    except Exception as e:
        failed = True
        print(
            f"[AUTH {process_id}] Master worker has encountered an irrecovarable error, details: "
        )
        print(traceback.format_exc())
        synced_store_client.set(SyncedStoreStrings.ABORT, 1, ex=120)
        raise RuntimeError("Master bootup failed") from e
    finally:
        # Finally, initialize valid_keys list and remove the flag from Redis to allow slave workers to continue bootup
        if not (active_kid and rotated_verifying_keys):
            return

        if failed:
            return

        async with synced_store_client.pipeline() as pipe:
            pipe.delete(SyncedStoreStrings.VALID_KEYS)
            valid_keys: list[str] = list(rotated_verifying_keys.keys()) + [active_kid]
            pipe.lpush(SyncedStoreStrings.VALID_KEYS, *valid_keys)
            pipe.delete(SyncedStoreStrings.AUTH_BOOTUP_MASTER)
            await pipe.execute()


async def slave_bootup(
    config: AppConfig,
    synced_store_client: Redis,
    keydata_repository: KeydataRepository,
    process_id: int,
    master_wait_interval: float = 1.0,
) -> None:
    # Wait for master worker to finish managing key synchronization and file I/O, and then proceed on the assumption that the JWKS file has been written into/validated.
    while synced_store_client.get(SyncedStoreStrings.AUTH_BOOTUP_MASTER):
        time.sleep(master_wait_interval)

    if synced_store_client.get(SyncedStoreStrings.ABORT):
        print(
            f"[AUTH {process_id}] Master failed to setup key configuration, aborting..."
        )
        raise RuntimeError("Master failed to set up key configuration")

    # Once lock is released, slave worker only needs to consult database and write to its own memory

    keys: list[KeyData] = await keydata_repository.get_relevant_keydata(
        limit=config.JWKS.JWKS_CAP, raise_on_empty=True
    )

    active_keydata: KeyMetadata | None = None
    active_kid: str | None = None
    rotated_active_keys_mapping: dict[str, KeyMetadata] = {}

    for key in keys:
        if key.rotated_out_at:
            # Verification Key
            rotated_active_keys_mapping[key.kid] = KeyMetadata(
                key.public_pem,
                key.private_pem,
                key.alg,
                key.epoch.timestamp(),
                key.rotated_out_at.timestamp(),
            )
        else:
            active_kid = key.kid
            active_keydata = KeyMetadata(
                key.public_pem, key.private_pem, key.alg, key.epoch.timestamp()
            )

    if not (active_kid and active_keydata):
        raise RuntimeError("No active key found")

    token_manager: Final[TokenManager] = get_token_manager()
    token_manager.set_key_state(active_kid, active_keydata, rotated_active_keys_mapping)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    PID: Final[int] = os.getpid()

    config: Final[AppConfig] = get_app_config()
    synced_store_client: Final[Redis] = get_synced_store_client()

    # Additional filepaths depending on instance/static directories
    config.JWKS.resolve_jwks_directory(config.CORE.instance_path)
    config.JWKS.resolve_public_pem_directory(config.CORE.static_path)
    config.JWKS.resolve_private_pem_directory(config.CORE.instance_path)

    # Error handler
    app.add_exception_handler(Exception, generic_error_handler)

    keydata_repository: Final[KeydataRepository] = KeydataRepository(
        get_database_session_maker()
    )

    is_master: bool = bool(
        synced_store_client.set(SyncedStoreStrings.AUTH_BOOTUP_MASTER, PID, nx=True)
    )

    if is_master:
        await master_bootup(config, synced_store_client, keydata_repository, PID)
    else:
        await slave_bootup(config, synced_store_client, keydata_repository, PID)

    register_routers(app, ROUTER_URL_MAPPING, config.CORE.APPLICATION_ROOT)

    yield
