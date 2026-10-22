import asyncio
import logging
import os
import traceback
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Final, Mapping

from auxillary.data_structures.locks.lock import (
    BasicLockContext,
    RedisInstanceLockFactory,
)
from auxillary.security.serialization import (
    pem_serialize_private_key,
    pem_serialize_public_key,
)
from auxillary.utils import ExceptionHandler
from fastapi import APIRouter, FastAPI
from redis.asyncio import Redis

from auth_server.config.app_config import AppConfig
from auth_server.config.sub_config import KeyConfigModel
from auth_server.datastructures.exceptions.keys import EmptyKeysError
from auth_server.dependencies.local import (
    get_app_config,
    get_database_session_maker,
    get_distributed_lock_factory,
    get_key_lifecycle_manager,
    get_synced_store_client,
    get_synced_store_key_state_manager,
    get_token_manager,
)
from auth_server.keys.keygen import generate_ecdsa_pair
from auth_server.models.data_transport.keydata import (
    ActiveKeyPrivateDataResult,
    RotatedKeyDataResult,
)
from auth_server.repositories.keydata import (
    KeydataRepository,
)
from auth_server.routers import RouterName, URLPrefix
from auth_server.strings import SyncedStoreCommandStrings
from auth_server.subsystems.key_manager import (
    KeyLifecycleManager,
    SyncedStoreKeyStateManager,
)
from auth_server.subsystems.token_manager import TokenManager


def register_exception_handler(
    app: FastAPI, exception_handler_mapping: Mapping[type[Exception], ExceptionHandler]
) -> None:
    for exception, handler in exception_handler_mapping.items():
        app.add_exception_handler(exception, handler)


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
) -> ActiveKeyPrivateDataResult:
    key_id, private_key, public_key = generate_ecdsa_pair(key_config)
    return await keydata_repository.insert_keydata(
        key_id,
        pem_serialize_private_key(private_key, key_config.PRIVATE_PEM_FORMAT),
        pem_serialize_public_key(public_key, key_config.PUBLIC_PEM_FORMAT),
        key_config.SIGNATURE_ALGORITHM,
        key_config.EC_TYPE,
        returning=True,
    )


async def master_bootup(
    config: AppConfig,
    synced_store_client: Redis,
    synced_store_manager: SyncedStoreKeyStateManager,
    keydata_repository: KeydataRepository,
    token_manager: TokenManager,
    process_id: int,
    *,
    logger: logging.Logger | None = None,
) -> None:
    logger = logger or logging.getLogger(f"{process_id}::{master_bootup.__name__}")
    logger.info("Serving as master")

    active_keydata: ActiveKeyPrivateDataResult | None = None
    rotated_verifying_keys: list[RotatedKeyDataResult] = []
    try:
        try:
            keydata: tuple[
                ActiveKeyPrivateDataResult, list[RotatedKeyDataResult]
            ] = await keydata_repository.get_relevant_keydata(
                active_key_public_data_only=False
            )
            active_keydata = keydata[0]
            rotated_verifying_keys = keydata[1]
            del keydata
        except EmptyKeysError:
            # No valid keys in DB, master must create new pair
            logger.info("Creating new key pair")
            active_keydata = await _initialize_active_key(
                config.KEYS, keydata_repository
            )
        if len(rotated_verifying_keys) > config.JWKS.JWKS_CAP - 1:
            await keydata_repository.expire_keydata_with_threshold(
                rotated_verifying_keys[config.JWKS.JWKS_CAP - 1].epoch
            )
            rotated_verifying_keys = rotated_verifying_keys[: config.JWKS.JWKS_CAP - 1]
        await synced_store_manager.set_jwks(await keydata_repository.get_jwks())
        token_manager.set_key_state(
            active_keydata, {i.kid: i for i in rotated_verifying_keys}
        )
        logger.info("Master process bootup complete!")
    except Exception as e:
        logger.error("Master worker has encountered an irrecoverable error, details:")
        logger.error(traceback.format_exc())
        await synced_store_client.set(
            SyncedStoreCommandStrings.ABORT,
            1,
            ex=token_manager.token_manager_config.ANNOUNCEMENT_DURATION,
        )
        raise RuntimeError("Master bootup failed") from e
    finally:
        await synced_store_client.delete(SyncedStoreCommandStrings.AUTH_BOOTUP_MASTER)


async def slave_bootup(
    config: AppConfig,
    synced_store_client: Redis,
    keydata_repository: KeydataRepository,
    token_manager: TokenManager,
    process_id: int,
    *,
    logger: logging.Logger | None = None,
) -> None:
    logger = logger or logging.getLogger(f"{process_id}::{slave_bootup.__name__}")
    # Wait for master worker to finish managing key synchronization and file I/O, and then proceed on the assumption that the JWKS file has been written into/validated.
    while await synced_store_client.get(SyncedStoreCommandStrings.AUTH_BOOTUP_MASTER):  # noqa
        await asyncio.sleep(config.BOOTUP.SLAVE_SLEEP_POLLING_INTERVAL.total_seconds())  # noqa

    if await synced_store_client.get(SyncedStoreCommandStrings.ABORT):
        logger.error("Master failed to setup key configuration, aborting...")
        raise RuntimeError("Master failed to set up key configuration")

    keydata: tuple[
        ActiveKeyPrivateDataResult, list[RotatedKeyDataResult]
    ] = await keydata_repository.get_relevant_keydata(active_key_public_data_only=False)
    token_manager.set_key_state(keydata[0], {k.kid: k for k in keydata[1]})


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    pid: Final[int] = os.getpid()

    config: Final[AppConfig] = get_app_config()
    synced_store_client: Final[Redis] = get_synced_store_client()
    lock_factory: Final[RedisInstanceLockFactory] = get_distributed_lock_factory()

    keydata_repository: Final[KeydataRepository] = KeydataRepository(
        get_database_session_maker()
    )
    token_manager: Final[TokenManager] = get_token_manager()
    synced_state_manager: Final[SyncedStoreKeyStateManager] = (
        get_synced_store_key_state_manager()
    )
    lifecycle_manager: Final[KeyLifecycleManager] = get_key_lifecycle_manager()

    master_lock_context: Final[BasicLockContext] = await lock_factory.lock(
        SyncedStoreCommandStrings.AUTH_BOOTUP_MASTER,
        str(pid),
        int(config.BOOTUP.MASTER_BOOTUP_LOCK_LIFESPAN.total_seconds()),
    )

    if master_lock_context.valid:
        async with master_lock_context:
            await master_bootup(
                config,
                synced_store_client,
                synced_state_manager,
                keydata_repository,
                token_manager,
                pid,
            )
    else:
        await slave_bootup(
            config, synced_store_client, keydata_repository, token_manager, pid
        )

    lifecycle_manager.start_token_manager_polling()
    yield
    await lifecycle_manager.stop_token_manager_polling()
