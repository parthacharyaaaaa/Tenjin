import asyncio
import multiprocessing
import os
import traceback
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from multiprocessing.context import BaseContext
from multiprocessing.process import BaseProcess
from queue import Empty
from types import SimpleNamespace
from typing import Any, Final, Literal, Protocol, cast

import pytest
import pytest_asyncio
from auth_server.models.database import KeyData
from auth_server.repositories.keydata import KeydataRepository
from auth_server.strings import SyncedStoreCommandStrings
from auth_server.subsystems.key_manager import SyncedStoreKeyStateManager
from auth_server.subsystems.token_manager import TokenManager
from auth_server.utils.bootup import master_bootup, slave_bootup
from auxillary.data_structures.locks.lock import RedisInstanceLockFactory
from auxillary.security.data_structures.jwks.enums import ECAlg
from auxillary.security.serialization import (
    pem_serialize_private_key,
    pem_serialize_public_key,
)
from cryptography.hazmat.primitives.asymmetric.ec import (
    SECP256R1,
    derive_private_key,
)
from cryptography.hazmat.primitives.serialization import PrivateFormat, PublicFormat
from redis.asyncio.client import Redis
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

PROCESS_TIMEOUT: Final[float] = 20
WORKER_COUNT: Final[int] = 3


class ProcessBarrier(Protocol):
    def wait(self, timeout: float | None = None) -> int: ...


class ProcessQueue(Protocol):
    def put(self, obj: object) -> None: ...

    def get(self, block: bool = True, timeout: float | None = None) -> object: ...


@dataclass(frozen=True, slots=True)
class RedisEndpoint:
    host: str
    port: int
    db: int
    username: str | None
    password: str | None


@dataclass(frozen=True, slots=True)
class ProcessServices:
    database_url: str
    synced_store: RedisEndpoint
    token_store: RedisEndpoint
    key_identifier_length: int
    signature_algorithm: str
    token_config: SimpleNamespace
    jwks_cap: int
    slave_poll_interval: timedelta
    lock_ttl: int = 30


@dataclass(frozen=True, slots=True)
class BootResult:
    role: Literal["master", "slave", "unassigned"]
    status: Literal["ok", "error"]
    process_id: int
    active_key_id: str | None
    key_ids: tuple[str, ...]
    error_type: str | None = None
    error_message: str | None = None
    traceback_text: str | None = None


def _redis_endpoint(client: Redis) -> RedisEndpoint:
    connection_kwargs: dict[str, Any] = client.connection_pool.connection_kwargs
    return RedisEndpoint(
        host=cast(str, connection_kwargs["host"]),
        port=int(connection_kwargs["port"]),
        db=int(connection_kwargs.get("db", 0)),
        username=cast(str | None, connection_kwargs.get("username")),
        password=cast(str | None, connection_kwargs.get("password")),
    )


def _redis_client(endpoint: RedisEndpoint) -> Redis:
    return Redis(
        host=endpoint.host,
        port=endpoint.port,
        db=endpoint.db,
        username=endpoint.username,
        password=endpoint.password,
        decode_responses=True,
    )


async def _execute_elected_bootup(
    services: ProcessServices,
    barrier: ProcessBarrier,
) -> BootResult:
    process_id: Final[int] = os.getpid()
    role: Literal["master", "slave", "unassigned"] = "unassigned"
    engine: AsyncEngine | None = None
    synced_store_client: Redis | None = None
    token_store_client: Redis | None = None
    token_manager: TokenManager | None = None

    try:
        engine = create_async_engine(services.database_url)
        session_maker: Final[async_sessionmaker[AsyncSession]] = async_sessionmaker(
            bind=engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=True,
        )
        repository: Final[KeydataRepository] = KeydataRepository(session_maker)
        synced_store_client = _redis_client(services.synced_store)
        token_store_client = _redis_client(services.token_store)
        key_config = SimpleNamespace(
            KEY_IDENTIFIER_LENGTH=services.key_identifier_length,
            SIGNATURE_ALGORITHM=services.signature_algorithm,
            EC_TYPE=SECP256R1(),
            PRIVATE_PEM_FORMAT=PrivateFormat.PKCS8,
            PUBLIC_PEM_FORMAT=PublicFormat.SubjectPublicKeyInfo,
        )
        synced_store_manager: Final[SyncedStoreKeyStateManager] = (
            SyncedStoreKeyStateManager(synced_store_client)
        )
        token_manager = TokenManager(
            token_store_client,
            synced_store_client,
            repository,
            key_config,  # pyrefly: ignore[bad-argument-type]
            services.token_config,  # pyrefly: ignore[bad-argument-type]
        )
        config = SimpleNamespace(
            KEYS=key_config,
            JWKS=SimpleNamespace(JWKS_CAP=services.jwks_cap),
            BOOTUP=SimpleNamespace(
                SLAVE_SLEEP_POLLING_INTERVAL=services.slave_poll_interval
            ),
        )
        lock_factory = RedisInstanceLockFactory(synced_store_client)
        lock = await lock_factory.lock(
            SyncedStoreCommandStrings.AUTH_BOOTUP_MASTER,
            str(process_id),
            services.lock_ttl,
        )
        role = "master" if lock.valid else "slave"

        # Every worker decides its role before any master is allowed to release the lock.
        barrier.wait(timeout=PROCESS_TIMEOUT)

        if lock.valid:
            async with lock:
                await master_bootup(
                    config,  # pyrefly: ignore[bad-argument-type]
                    synced_store_client,
                    synced_store_manager,
                    repository,
                    token_manager,
                    process_id,
                )
        else:
            await slave_bootup(
                config,  # pyrefly: ignore[bad-argument-type]
                synced_store_client,
                repository,
                token_manager,
                process_id,
            )

        return BootResult(
            role=role,
            status="ok",
            process_id=process_id,
            active_key_id=token_manager._active_key,
            key_ids=tuple(sorted(token_manager._key_mapping)),
        )
    except Exception as exc:
        return BootResult(
            role=role,
            status="error",
            process_id=process_id,
            active_key_id=(
                None
                if token_manager is None
                or token_manager._active_key == "__UNINITIALIZED__"
                else token_manager._active_key
            ),
            key_ids=(
                tuple()
                if token_manager is None
                else tuple(sorted(token_manager._key_mapping))
            ),
            error_type=type(exc).__name__,
            error_message=str(exc),
            traceback_text=traceback.format_exc(),
        )
    finally:
        if token_store_client is not None:
            await token_store_client.aclose()
        if synced_store_client is not None:
            await synced_store_client.aclose()
        if engine is not None:
            await engine.dispose()


def _bootup_process_entrypoint(
    services: ProcessServices,
    barrier: ProcessBarrier,
    result_queue: ProcessQueue,
) -> None:
    result_queue.put(asyncio.run(_execute_elected_bootup(services, barrier)))


def _run_processes(
    context: BaseContext,
    services: ProcessServices,
    worker_count: int,
) -> list[BootResult]:
    barrier = context.Barrier(worker_count)
    result_queue = context.Queue()
    processes: list[BaseProcess] = [
        context.Process(  # pyrefly: ignore[missing-attribute]
            target=_bootup_process_entrypoint,
            args=(services, barrier, result_queue),
        )
        for _ in range(worker_count)
    ]

    for process in processes:
        process.start()

    results: list[BootResult] = []
    try:
        for _ in processes:
            try:
                result = result_queue.get(timeout=PROCESS_TIMEOUT)
            except Empty as exc:
                raise AssertionError("Timed out waiting for bootup worker") from exc
            assert isinstance(result, BootResult)
            results.append(result)
    finally:
        for process in processes:
            process.join(timeout=PROCESS_TIMEOUT)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    assert all(process.exitcode == 0 for process in processes)
    return results


@pytest.fixture(scope="module")
def process_context() -> BaseContext:
    return multiprocessing.get_context("spawn")


@pytest.fixture
def process_services(
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
    async_redis_client: Redis,
    token_store_client: Redis,
    dummy_key_config: SimpleNamespace,
    dummy_token_manager_config: SimpleNamespace,
    dummy_jwks_config: SimpleNamespace,
) -> ProcessServices:
    engine = cast(AsyncEngine, pg_async_sessionmaker.kw["bind"])
    return ProcessServices(
        database_url=engine.url.render_as_string(hide_password=False),
        synced_store=_redis_endpoint(async_redis_client),
        token_store=_redis_endpoint(token_store_client),
        key_identifier_length=dummy_key_config.KEY_IDENTIFIER_LENGTH,
        signature_algorithm=dummy_key_config.SIGNATURE_ALGORITHM,
        token_config=dummy_token_manager_config,
        jwks_cap=dummy_jwks_config.JWKS_CAP,
        slave_poll_interval=timedelta(milliseconds=10),
    )


@pytest_asyncio.fixture(autouse=True)
async def clean_bootup_state(
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
    async_redis_client: Redis,
    token_store_client: Redis,
) -> None:
    await async_redis_client.flushall()
    await token_store_client.flushall()
    async with pg_async_sessionmaker() as session:
        await session.execute(delete(KeyData))
        await session.commit()


async def _seed_key_history(
    repository: KeydataRepository,
    key_config: SimpleNamespace,
    key_count: int,
) -> tuple[str, ...]:
    key_ids = tuple(f"seeded-{index}" for index in range(1, key_count + 1))
    keys = [derive_private_key(index, SECP256R1()) for index in range(1, key_count + 1)]

    async with repository.unit_of_work():
        await repository.insert_keydata(
            key_ids[0],
            pem_serialize_private_key(keys[0], key_config.PRIVATE_PEM_FORMAT),
            pem_serialize_public_key(
                keys[0].public_key(), key_config.PUBLIC_PEM_FORMAT
            ),
            ECAlg.ES256,
            key_config.EC_TYPE,
            epoch=datetime(2026, 1, 1, tzinfo=UTC),
        )
        for index, (key_id, key) in enumerate(
            zip(key_ids[1:], keys[1:], strict=True),
            start=2,
        ):
            await repository.rotate_key(
                key_ids[index - 2],
                key_id,
                pem_serialize_public_key(
                    key.public_key(), key_config.PUBLIC_PEM_FORMAT
                ),
                pem_serialize_private_key(key, key_config.PRIVATE_PEM_FORMAT),
                alg=ECAlg.ES256,
                curve=key_config.EC_TYPE,
                epoch=datetime(2026, 1, index, tzinfo=UTC),
            )

    return key_ids


def _assert_successful_election(results: Sequence[BootResult]) -> None:
    assert [result.role for result in results].count("master") == 1
    assert [result.role for result in results].count("slave") == len(results) - 1
    assert all(result.status == "ok" for result in results), [
        result.traceback_text for result in results if result.status == "error"
    ]
    assert len({result.process_id for result in results}) == len(results)


@pytest.mark.asyncio
async def test_concurrent_processes_elect_master_and_share_clean_boot_state(
    process_context: BaseContext,
    process_services: ProcessServices,
    keydata_repository: KeydataRepository,
    synced_key_store_manager: SyncedStoreKeyStateManager,
    async_redis_client: Redis,
) -> None:
    results = _run_processes(process_context, process_services, WORKER_COUNT)

    _assert_successful_election(results)
    active_key_ids = {result.active_key_id for result in results}
    local_keysets = {result.key_ids for result in results}
    persisted_keys = await keydata_repository.get_relevant_keydata()
    synced_jwks = await synced_key_store_manager.get_jwks()

    assert len(active_key_ids) == 1
    assert None not in active_key_ids
    assert len(local_keysets) == 1
    assert len(persisted_keys) == 1
    assert persisted_keys[0].rotated_out_at is None
    assert {key.kid for key in persisted_keys} == active_key_ids
    assert {key.kid for key in synced_jwks} == active_key_ids
    assert (
        await async_redis_client.get(SyncedStoreCommandStrings.AUTH_BOOTUP_MASTER)
        is None
    )
    assert await async_redis_client.get(SyncedStoreCommandStrings.ABORT) is None


@pytest.mark.asyncio
async def test_populated_missing_active_state_is_repaired_at_cap_across_processes(
    process_context: BaseContext,
    process_services: ProcessServices,
    dummy_key_config: SimpleNamespace,
    keydata_repository: KeydataRepository,
    synced_key_store_manager: SyncedStoreKeyStateManager,
) -> None:
    key_ids = await _seed_key_history(
        keydata_repository,
        dummy_key_config,
        process_services.jwks_cap + 1,
    )
    await keydata_repository.expire_keydata(key_ids[-1])
    assert (
        len(await keydata_repository.get_relevant_keydata())
        == process_services.jwks_cap
    )
    assert await keydata_repository.get_active_key() is None

    results = _run_processes(process_context, process_services, worker_count=2)

    _assert_successful_election(results)
    relevant_keys = await keydata_repository.get_relevant_keydata()
    expired_keys = await keydata_repository.get_expired_keys()
    synced_jwks = await synced_key_store_manager.get_jwks()
    active_keys = [key for key in relevant_keys if key.rotated_out_at is None]
    expected_key_ids = {key.kid for key in relevant_keys}

    assert len(relevant_keys) == process_services.jwks_cap
    assert len(active_keys) == 1
    assert len(expired_keys) == 2
    assert key_ids[-1] in {key.kid for key in expired_keys}
    assert {key.kid for key in synced_jwks} == expected_key_ids
    assert all(result.active_key_id == active_keys[0].kid for result in results)
    assert all(set(result.key_ids) == expected_key_ids for result in results)


@pytest.mark.asyncio
async def test_master_database_failure_announces_abort_to_concurrent_slaves(
    process_context: BaseContext,
    process_services: ProcessServices,
    async_redis_client: Redis,
    keydata_repository: KeydataRepository,
) -> None:
    unavailable_database_services = replace(
        process_services,
        database_url=(
            "postgresql+psycopg://invalid:invalid@127.0.0.1:1/missing?connect_timeout=1"
        ),
    )

    results = _run_processes(
        process_context,
        unavailable_database_services,
        WORKER_COUNT,
    )

    master_result = next(result for result in results if result.role == "master")
    slave_results = [result for result in results if result.role == "slave"]
    assert len(slave_results) == WORKER_COUNT - 1
    assert master_result.status == "error"
    assert master_result.error_type == "RuntimeError"
    assert master_result.error_message == "Master bootup failed"
    assert all(result.status == "error" for result in slave_results)
    assert all(
        result.error_message == "Master failed to set up key configuration"
        for result in slave_results
    )
    assert (
        await async_redis_client.get(SyncedStoreCommandStrings.AUTH_BOOTUP_MASTER)
        is None
    )
    assert await async_redis_client.get(SyncedStoreCommandStrings.ABORT) == "1"
    assert await async_redis_client.ttl(SyncedStoreCommandStrings.ABORT) > 0
    assert await keydata_repository.get_relevant_keydata() == []
