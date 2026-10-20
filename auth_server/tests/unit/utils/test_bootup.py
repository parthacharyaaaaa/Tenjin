import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
from auth_server.repositories.keydata import (
    EllipticCurveJWKResult,
    KeyPrivateDataResult,
    KeyPublicDataResult,
)
from auth_server.strings import SyncedStoreCommandStrings
from auth_server.utils.bootup import master_bootup, slave_bootup
from auxillary.security.data_structures.jwks.enums import ECAlg


@dataclass
class BootHarness:
    config: SimpleNamespace
    store: Mock
    synced_store_manager: Mock
    repository: Mock
    token_manager: Mock
    logger: Mock
    calls: Mock


@pytest.fixture
def boot_harness() -> BootHarness:
    calls = Mock()
    store = Mock()
    synced_store_manager = Mock()
    repository = Mock()
    token_manager = Mock()
    logger = Mock()

    store.get = AsyncMock()
    store.set = AsyncMock()
    store.delete = AsyncMock()
    synced_store_manager.set_jwks = AsyncMock()
    repository.get_relevant_keydata = AsyncMock()
    repository.expire_keydata_with_threshold = AsyncMock()
    repository.get_jwks = AsyncMock()
    token_manager.set_key_state = Mock()
    token_manager.token_manager_config = SimpleNamespace(
        ANNOUNCEMENT_DURATION=timedelta(seconds=30)
    )

    calls.attach_mock(store.get, "get")
    calls.attach_mock(store.set, "set")
    calls.attach_mock(store.delete, "delete")
    calls.attach_mock(repository.get_relevant_keydata, "get_relevant")
    calls.attach_mock(repository.expire_keydata_with_threshold, "expire_threshold")
    calls.attach_mock(repository.get_jwks, "get_jwks")
    calls.attach_mock(synced_store_manager.set_jwks, "set_jwks")
    calls.attach_mock(token_manager.set_key_state, "set_key_state")

    config = SimpleNamespace(
        KEYS=SimpleNamespace(),
        JWKS=SimpleNamespace(JWKS_CAP=3),
        BOOTUP=SimpleNamespace(SLAVE_SLEEP_POLLING_INTERVAL=timedelta(milliseconds=25)),
    )
    return BootHarness(
        config,
        store,
        synced_store_manager,
        repository,
        token_manager,
        logger,
        calls,
    )


@pytest.fixture
def key_factory() -> Callable[[str, int, bool], KeyPrivateDataResult]:
    def make_key(kid: str, day: int, active: bool) -> KeyPrivateDataResult:
        key = Mock(spec=KeyPrivateDataResult)
        key.kid = kid
        key.epoch = datetime(2026, 1, day, tzinfo=UTC)
        key.rotated_out_at = None if active else datetime(2026, 2, day, tzinfo=UTC)
        key.create_public_copy.return_value = Mock(
            spec=KeyPublicDataResult,
            kid=kid,
        )
        return cast(KeyPrivateDataResult, key)

    return make_key


@pytest.fixture
def jwks() -> tuple[EllipticCurveJWKResult, ...]:
    return (
        EllipticCurveJWKResult(
            alg=ECAlg.ES256,
            crv="secp256r1",
            kid="active",
            x="x-coordinate",
            y="y-coordinate",
        ),
    )


async def run_master(harness: BootHarness, process_id: int = 101) -> None:
    await master_bootup(
        harness.config,  # pyrefly: ignore[bad-argument-type]
        harness.store,
        harness.synced_store_manager,
        harness.repository,
        harness.token_manager,
        process_id,
        logger=harness.logger,
    )


async def run_slave(harness: BootHarness, process_id: int = 202) -> None:
    await slave_bootup(
        harness.config,  # pyrefly: ignore[bad-argument-type]
        harness.store,
        harness.repository,
        harness.token_manager,
        process_id,
        logger=harness.logger,
    )


@pytest.mark.asyncio
async def test_master_initializes_clean_database_and_announces_key_state(
    boot_harness: BootHarness,
    key_factory: Callable[[str, int, bool], KeyPrivateDataResult],
    jwks: tuple[EllipticCurveJWKResult, ...],
) -> None:
    active_key = key_factory("active", 1, True)
    boot_harness.repository.get_relevant_keydata.return_value = []
    boot_harness.repository.get_jwks.return_value = jwks

    with patch(
        "auth_server.utils.bootup._initialize_active_key",
        new_callable=AsyncMock,
        return_value=active_key,
    ) as initialize_active_key:
        boot_harness.calls.attach_mock(initialize_active_key, "initialize_active")
        await run_master(boot_harness)

    assert boot_harness.calls.mock_calls == [
        call.get_relevant(public_data_only=False),
        call.initialize_active(boot_harness.config.KEYS, boot_harness.repository),
        call.get_jwks(),
        call.set_jwks(jwks),
        call.set_key_state(active_key, None),
        call.delete(SyncedStoreCommandStrings.AUTH_BOOTUP_MASTER),
    ]
    boot_harness.repository.expire_keydata_with_threshold.assert_not_awaited()
    boot_harness.logger.info.assert_any_call("Creating new key pair")


@pytest.mark.asyncio
async def test_master_reuses_populated_database_without_generating_or_expiring_keys(
    boot_harness: BootHarness,
    key_factory: Callable[[str, int, bool], KeyPrivateDataResult],
    jwks: tuple[EllipticCurveJWKResult, ...],
) -> None:
    active_key = key_factory("active", 3, True)
    rotated_key = key_factory("rotated", 2, False)
    boot_harness.repository.get_relevant_keydata.return_value = [
        active_key,
        rotated_key,
    ]
    boot_harness.repository.get_jwks.return_value = jwks

    with patch(
        "auth_server.utils.bootup._initialize_active_key",
        new_callable=AsyncMock,
    ) as initialize_active_key:
        await run_master(boot_harness)

    initialize_active_key.assert_not_awaited()
    boot_harness.repository.expire_keydata_with_threshold.assert_not_awaited()
    boot_harness.synced_store_manager.set_jwks.assert_awaited_once_with(jwks)
    boot_harness.token_manager.set_key_state.assert_called_once_with(
        active_key,
        {rotated_key.kid: rotated_key},
    )
    boot_harness.store.delete.assert_awaited_once_with(
        SyncedStoreCommandStrings.AUTH_BOOTUP_MASTER
    )


@pytest.mark.asyncio
async def test_master_expires_oldest_keys_when_populated_database_exceeds_cap(
    boot_harness: BootHarness,
    key_factory: Callable[[str, int, bool], KeyPrivateDataResult],
    jwks: tuple[EllipticCurveJWKResult, ...],
) -> None:
    active_key = key_factory("active", 5, True)
    rotated_keys = [
        key_factory("rotated-4", 4, False),
        key_factory("rotated-3", 3, False),
        key_factory("rotated-2", 2, False),
    ]
    boot_harness.repository.get_relevant_keydata.return_value = [
        active_key,
        *rotated_keys,
    ]
    boot_harness.repository.get_jwks.return_value = jwks

    await run_master(boot_harness)

    boot_harness.repository.expire_keydata_with_threshold.assert_awaited_once_with(
        rotated_keys[1].epoch
    )
    boot_harness.token_manager.set_key_state.assert_called_once_with(
        active_key,
        {
            rotated_keys[0].kid: rotated_keys[0],
            rotated_keys[1].kid: rotated_keys[1],
        },
    )


@pytest.mark.asyncio
async def test_master_replaces_missing_active_key_without_exceeding_cap(
    boot_harness: BootHarness,
    key_factory: Callable[[str, int, bool], KeyPrivateDataResult],
    jwks: tuple[EllipticCurveJWKResult, ...],
) -> None:
    replacement_active_key = key_factory("replacement", 6, True)
    rotated_keys = [
        key_factory("rotated-5", 5, False),
        key_factory("rotated-4", 4, False),
        key_factory("rotated-3", 3, False),
        key_factory("rotated-2", 2, False),
    ]
    boot_harness.repository.get_relevant_keydata.return_value = rotated_keys
    boot_harness.repository.get_jwks.return_value = jwks

    with patch(
        "auth_server.utils.bootup._initialize_active_key",
        new_callable=AsyncMock,
        return_value=replacement_active_key,
    ) as initialize_active_key:
        await run_master(boot_harness)

    initialize_active_key.assert_awaited_once_with(
        boot_harness.config.KEYS,
        boot_harness.repository,
    )
    boot_harness.repository.expire_keydata_with_threshold.assert_awaited_once_with(
        rotated_keys[1].epoch
    )
    boot_harness.token_manager.set_key_state.assert_called_once_with(
        replacement_active_key,
        {
            rotated_keys[0].kid: rotated_keys[0],
            rotated_keys[1].kid: rotated_keys[1],
        },
    )


@pytest.mark.asyncio
async def test_master_replaces_missing_active_key_below_cap_without_expiry(
    boot_harness: BootHarness,
    key_factory: Callable[[str, int, bool], KeyPrivateDataResult],
    jwks: tuple[EllipticCurveJWKResult, ...],
) -> None:
    replacement_active_key = key_factory("replacement", 3, True)
    rotated_keys = [
        key_factory("rotated-2", 2, False),
        key_factory("rotated-1", 1, False),
    ]
    expected_rotated_keys = {key.kid: key for key in rotated_keys}
    boot_harness.repository.get_relevant_keydata.return_value = rotated_keys
    boot_harness.repository.get_jwks.return_value = jwks

    with patch(
        "auth_server.utils.bootup._initialize_active_key",
        new_callable=AsyncMock,
        return_value=replacement_active_key,
    ):
        await run_master(boot_harness)

    boot_harness.repository.expire_keydata_with_threshold.assert_not_awaited()
    boot_harness.token_manager.set_key_state.assert_called_once_with(
        replacement_active_key,
        expected_rotated_keys,
    )


@pytest.mark.asyncio
async def test_master_announces_abort_and_releases_lock_after_failure(
    boot_harness: BootHarness,
) -> None:
    failure = ValueError("database unavailable")
    boot_harness.repository.get_relevant_keydata.side_effect = failure

    with pytest.raises(RuntimeError, match="Master bootup failed") as exc_info:
        await run_master(boot_harness)

    assert exc_info.value.__cause__ is failure
    assert boot_harness.calls.mock_calls == [
        call.get_relevant(public_data_only=False),
        call.set(
            SyncedStoreCommandStrings.ABORT,
            1,
            ex=timedelta(seconds=30),
        ),
        call.delete(SyncedStoreCommandStrings.AUTH_BOOTUP_MASTER),
    ]
    assert boot_harness.logger.error.call_count == 2


@pytest.mark.asyncio
async def test_slave_loads_active_and_public_verification_keys(
    boot_harness: BootHarness,
    key_factory: Callable[[str, int, bool], KeyPrivateDataResult],
) -> None:
    active_key = key_factory("active", 3, True)
    rotated_keys = [
        key_factory("rotated-2", 2, False),
        key_factory("rotated-1", 1, False),
    ]
    boot_harness.store.get.side_effect = (None, None)
    boot_harness.repository.get_relevant_keydata.return_value = [
        active_key,
        *rotated_keys,
    ]

    await run_slave(boot_harness)

    boot_harness.repository.get_relevant_keydata.assert_awaited_once_with(
        limit=3,
        raise_on_empty=True,
        public_data_only=False,
    )
    boot_harness.token_manager.set_key_state.assert_called_once_with(
        active_key,
        {
            key.kid: cast(Mock, key.create_public_copy).return_value
            for key in rotated_keys
        },
    )
    cast(Mock, active_key.create_public_copy).assert_not_called()


@pytest.mark.asyncio
async def test_slave_polls_until_master_releases_lock(
    boot_harness: BootHarness,
    key_factory: Callable[[str, int, bool], KeyPrivateDataResult],
) -> None:
    active_key = key_factory("active", 1, True)
    boot_harness.store.get.side_effect = ("master", "master", None, None)
    boot_harness.repository.get_relevant_keydata.return_value = [active_key]

    with patch(
        "auth_server.utils.bootup.asyncio.sleep",
        new_callable=AsyncMock,
    ) as sleep:
        await run_slave(boot_harness)

    assert sleep.await_args_list == [call(0.025), call(0.025)]
    assert boot_harness.store.get.await_args_list == [
        call(SyncedStoreCommandStrings.AUTH_BOOTUP_MASTER),
        call(SyncedStoreCommandStrings.AUTH_BOOTUP_MASTER),
        call(SyncedStoreCommandStrings.AUTH_BOOTUP_MASTER),
        call(SyncedStoreCommandStrings.ABORT),
    ]
    boot_harness.token_manager.set_key_state.assert_called_once_with(active_key, {})


@pytest.mark.asyncio
async def test_slave_aborts_after_master_failure(
    boot_harness: BootHarness,
) -> None:
    boot_harness.store.get.side_effect = ("master", None, 1)

    with patch(
        "auth_server.utils.bootup.asyncio.sleep",
        new_callable=AsyncMock,
    ) as sleep:
        with pytest.raises(
            RuntimeError,
            match="Master failed to set up key configuration",
        ):
            await run_slave(boot_harness)

    sleep.assert_awaited_once_with(0.025)
    boot_harness.repository.get_relevant_keydata.assert_not_awaited()
    boot_harness.token_manager.set_key_state.assert_not_called()
    boot_harness.logger.error.assert_called_once_with(
        "Master failed to setup key configuration, aborting..."
    )


@pytest.mark.asyncio
async def test_slave_propagates_empty_database_failure(
    boot_harness: BootHarness,
) -> None:
    failure = ValueError("Key data empty")
    boot_harness.store.get.side_effect = (None, None)
    boot_harness.repository.get_relevant_keydata.side_effect = failure

    with pytest.raises(ValueError, match="Key data empty") as exc_info:
        await run_slave(boot_harness)

    assert exc_info.value is failure
    boot_harness.token_manager.set_key_state.assert_not_called()


@pytest.mark.asyncio
async def test_slave_rejects_database_without_active_key(
    boot_harness: BootHarness,
    key_factory: Callable[[str, int, bool], KeyPrivateDataResult],
) -> None:
    rotated_key = key_factory("rotated", 1, False)
    boot_harness.store.get.side_effect = (None, None)
    boot_harness.repository.get_relevant_keydata.return_value = [rotated_key]

    with pytest.raises(RuntimeError, match="No active key found"):
        await run_slave(boot_harness)

    cast(Mock, rotated_key.create_public_copy).assert_called_once_with()
    boot_harness.token_manager.set_key_state.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_slaves_wait_for_master_cleanup_before_loading_keys(
    boot_harness: BootHarness,
    key_factory: Callable[[str, int, bool], KeyPrivateDataResult],
    jwks: tuple[EllipticCurveJWKResult, ...],
) -> None:
    active_key = key_factory("active", 2, True)
    rotated_key = key_factory("rotated", 1, False)
    master_started = asyncio.Event()
    release_master = asyncio.Event()
    both_slaves_waiting = asyncio.Event()
    state = {"locked": True, "waiting_slaves": 0}

    async def master_keydata(**_: object) -> list[KeyPrivateDataResult]:
        master_started.set()
        await release_master.wait()
        return [active_key, rotated_key]

    async def get_synced_value(key: str) -> str | None:
        if key == SyncedStoreCommandStrings.AUTH_BOOTUP_MASTER:
            if state["locked"]:
                state["waiting_slaves"] += 1
                if state["waiting_slaves"] >= 2:
                    both_slaves_waiting.set()
                return "master"
            return None
        return None

    async def release_lock(key: str) -> None:
        assert key == SyncedStoreCommandStrings.AUTH_BOOTUP_MASTER
        state["locked"] = False

    async def slave_keydata(**_: object) -> list[KeyPrivateDataResult]:
        assert state["locked"] is False
        return [active_key, rotated_key]

    master_repository = Mock()
    master_repository.get_relevant_keydata = AsyncMock(side_effect=master_keydata)
    master_repository.get_jwks = AsyncMock(return_value=jwks)
    boot_harness.store.get.side_effect = get_synced_value
    boot_harness.store.delete.side_effect = release_lock

    slave_repositories = (Mock(), Mock())
    slave_token_managers = (Mock(), Mock())
    for repository in slave_repositories:
        repository.get_relevant_keydata = AsyncMock(side_effect=slave_keydata)
    for token_manager in slave_token_managers:
        token_manager.set_key_state = Mock()

    real_sleep = asyncio.sleep

    async def cooperative_sleep(_: float) -> None:
        await real_sleep(0)

    master_task = asyncio.create_task(
        master_bootup(
            boot_harness.config,  # pyrefly: ignore[bad-argument-type]
            boot_harness.store,
            boot_harness.synced_store_manager,
            master_repository,
            boot_harness.token_manager,
            100,
            logger=boot_harness.logger,
        )
    )
    await asyncio.wait_for(master_started.wait(), timeout=1)

    with patch(
        "auth_server.utils.bootup.asyncio.sleep",
        new_callable=AsyncMock,
        side_effect=cooperative_sleep,
    ) as sleep:
        slave_tasks = [
            asyncio.create_task(
                slave_bootup(
                    boot_harness.config,  # pyrefly: ignore[bad-argument-type]
                    boot_harness.store,
                    repository,
                    token_manager,
                    process_id,
                    logger=boot_harness.logger,
                )
            )
            for process_id, repository, token_manager in zip(
                (201, 202),
                slave_repositories,
                slave_token_managers,
                strict=True,
            )
        ]
        await asyncio.wait_for(both_slaves_waiting.wait(), timeout=1)
        release_master.set()
        await asyncio.gather(master_task, *slave_tasks)

    assert sleep.await_count >= 2
    assert state["locked"] is False
    for repository in slave_repositories:
        repository.get_relevant_keydata.assert_awaited_once_with(
            limit=3,
            raise_on_empty=True,
            public_data_only=False,
        )
    for token_manager in slave_token_managers:
        token_manager.set_key_state.assert_called_once_with(
            active_key,
            {rotated_key.kid: cast(Mock, rotated_key.create_public_copy).return_value},
        )
