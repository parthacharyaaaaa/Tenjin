import asyncio
import os
import traceback
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Final, Mapping

from auxillary.utils import generic_error_handler
from fastapi import APIRouter, FastAPI
from redis.asyncio import Redis

from auth_server.config.app_config import AppConfig
from auth_server.config.sub_config import KeyConfigModel
from auth_server.dependencies import (
    get_app_config,
    get_database_session_maker,
    get_filesystem_key_manager,
    get_synced_store_client,
    get_token_manager,
)
from auth_server.repositories.keydata import (
    KeydataRepository,
    KeyPrivateDataResult,
    KeyPublicDataResult,
)
from auth_server.routers import ROUTER_URL_MAPPING, RouterName, URLPrefix
from auth_server.security.key_manager import FileSystemKeyManager
from auth_server.security.keygen import generate_ecdsa_pair
from auth_server.security.token_manager import TokenManager
from auth_server.strings import SyncedStoreStrings

# TODO: Remove magic numbers in lifespan and master_bootup (lock and flag TTLs)


def register_routers(
    app: FastAPI,
    url_prefix_mapping: Mapping[RouterName, tuple[APIRouter, tuple[URLPrefix, ...]]],
    common_prefix: str = "",
) -> None:
    for router, url_prefixes in url_prefix_mapping.values():
        app.include_router(
            router, prefix="/".join((common_prefix, *[u.value for u in url_prefixes]))
        )


async def _initialize_active_key(
    key_config: KeyConfigModel, keydata_repository: KeydataRepository
) -> KeyPrivateDataResult:
    key_id, private_key, public_key = generate_ecdsa_pair(key_config)
    return await keydata_repository.insert_keydata(
        key_id, private_key, public_key, "ES256", key_config.EC_TYPE, returning=True
    )


async def master_bootup(
    config: AppConfig,
    synced_store_client: Redis,
    keydata_repository: KeydataRepository,
    filesystem_key_manager: FileSystemKeyManager,
    token_manager: TokenManager,
    process_id: int,
) -> None:
    print(f"[AUTH {process_id}] Serving as master")

    active_keydata: KeyPrivateDataResult | None = None
    rotated_verifying_keys: dict[str, KeyPublicDataResult] | None = None

    try:
        keydata: list[
            KeyPrivateDataResult
        ] = await keydata_repository.get_relevant_keydata(public_data_only=False)
        if not keydata:
            # No valid keys in DB, master must create new pair
            print(f"[AUTH {process_id}] Creating new key pair")
            active_keydata = await _initialize_active_key(
                config.KEYS, keydata_repository
            )
            keydata.append(active_keydata)
        else:
            verification_only_keys: list[KeyPrivateDataResult] = list(
                filter(lambda x: x.rotated_out_at is not None, keydata)
            )
            missing_active: bool = len(keydata) == len(verification_only_keys)

            # Atleast 1 non-expired key exists in DB
            if len(keydata) > config.JWKS.JWKS_CAP:
                await keydata_repository.expire_keydata_with_threshold(
                    keydata[config.JWKS.JWKS_CAP - (1 + missing_active)].epoch
                )
                keydata = keydata[: config.JWKS.JWKS_CAP]

            if missing_active:
                active_keydata = await _initialize_active_key(
                    config.KEYS, keydata_repository
                )
                keydata.insert(0, active_keydata)
            else:
                active_keydata = keydata[0]

            if len(keydata) > 1:
                rotated_verifying_keys = {k.kid: k for k in keydata[1:]}

        await filesystem_key_manager.initialize_jwks(keydata)

        # Initialize token manager
        async with synced_store_client.pipeline() as pipe:
            pipe.delete(SyncedStoreStrings.VALID_KEYS)
            valid_keys: list[str] = (
                list(rotated_verifying_keys.keys()) if rotated_verifying_keys else []
            ) + [active_keydata.kid]
            pipe.lpush(SyncedStoreStrings.VALID_KEYS, *valid_keys)
            await pipe.execute()

        token_manager.set_key_state(active_keydata, rotated_verifying_keys)
        print(f"[AUTH {process_id}] Master process bootup complete!")
    except Exception as e:
        print(
            f"[AUTH {process_id}] Master worker has encountered an irrecoverable error, details: "
        )
        print(traceback.format_exc())
        await synced_store_client.set(
            SyncedStoreStrings.ABORT,
            1,
            ex=token_manager.token_manager_config.ANNOUNCEMENT_DURATION,
        )
        raise RuntimeError("Master bootup failed") from e
    finally:
        await synced_store_client.delete(SyncedStoreStrings.AUTH_BOOTUP_MASTER)


async def slave_bootup(
    config: AppConfig,
    synced_store_client: Redis,
    keydata_repository: KeydataRepository,
    token_manager: TokenManager,
    process_id: int,
    master_wait_interval: float = 1.0,
) -> None:
    # Wait for master worker to finish managing key synchronization and file I/O, and then proceed on the assumption that the JWKS file has been written into/validated.
    while await synced_store_client.get(SyncedStoreStrings.AUTH_BOOTUP_MASTER):  # noqa
        await asyncio.sleep(master_wait_interval)  # noqa

    if await synced_store_client.get(SyncedStoreStrings.ABORT):
        print(
            f"[AUTH {process_id}] Master failed to setup key configuration, aborting..."
        )
        raise RuntimeError("Master failed to set up key configuration")

    # Once lock is released, slave worker only needs to consult database and write to its own memory

    keys: list[KeyPrivateDataResult] = await keydata_repository.get_relevant_keydata(
        limit=config.JWKS.JWKS_CAP, raise_on_empty=True, public_data_only=False
    )

    active_keydata: KeyPrivateDataResult | None = None
    rotated_active_keys_mapping: dict[str, KeyPublicDataResult] = {}

    for key in keys:
        if key.rotated_out_at:
            # Verification Key
            rotated_active_keys_mapping[key.kid] = key.create_public_copy()
        else:
            active_keydata = key

    if not active_keydata:
        raise RuntimeError("No active key found")

    token_manager.set_key_state(active_keydata, rotated_active_keys_mapping)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    pid: Final[int] = os.getpid()

    config: Final[AppConfig] = get_app_config()
    synced_store_client: Final[Redis] = get_synced_store_client()

    # Additional filepaths depending on instance/static directories
    config.JWKS.resolve_jwks_filepath(config.CORE.instance_path)

    # Error handler
    app.add_exception_handler(Exception, generic_error_handler)

    keydata_repository: Final[KeydataRepository] = KeydataRepository(
        get_database_session_maker()
    )
    token_manager: Final[TokenManager] = get_token_manager()
    filesystem_key_manager: Final[FileSystemKeyManager] = get_filesystem_key_manager()

    is_master: bool = bool(
        await synced_store_client.set(
            SyncedStoreStrings.AUTH_BOOTUP_MASTER, pid, nx=True, ex=300
        )
    )

    if is_master:
        await master_bootup(
            config,
            synced_store_client,
            keydata_repository,
            filesystem_key_manager,
            token_manager,
            pid,
        )
    else:
        await slave_bootup(
            config, synced_store_client, keydata_repository, token_manager, pid
        )

    register_routers(app, ROUTER_URL_MAPPING, config.CORE.APPLICATION_ROOT)

    yield
