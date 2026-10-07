from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
from auth_server.strings import SelectionLockOption, SyncedStoreKeyStrings
from auth_server.subsystems.key_manager import KeyLifecycleManager, LockTIme


class AsyncContext:
    def __init__(self, value: object = None) -> None:
        self.enter = AsyncMock(return_value=value)
        self.exit = AsyncMock(return_value=None)

    async def __aenter__(self) -> object:
        return await self.enter()

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.exit(exc_type, exc_value, traceback)


def make_async_context(value: object = None) -> AsyncContext:
    return AsyncContext(value)


@dataclass
class LifecycleHarness:
    manager: KeyLifecycleManager
    repository: Mock
    token_manager: Mock
    key_config: SimpleNamespace
    synced_manager: Mock
    lock_factory: Mock
    announcer: Mock
    repository_context: AsyncContext


@pytest.fixture
def lifecycle() -> LifecycleHarness:
    repository = Mock()
    token_manager = Mock()
    key_config = SimpleNamespace(
        MAX_VALID_KEYS=2,
        PRIVATE_PEM_FORMAT="private-format",
        PUBLIC_PEM_FORMAT="public-format",
    )
    synced_manager = Mock()
    lock_factory = Mock()
    announcer = Mock()
    repository_context = make_async_context()

    repository.unit_of_work = Mock(return_value=repository_context)
    repository.get_keydata = AsyncMock()
    repository.expire_keydata = AsyncMock()
    repository.get_jwks = AsyncMock()
    repository.batch_expire_keydata = AsyncMock()
    repository.get_active_key = AsyncMock()
    repository.rotate_key = AsyncMock()
    repository.get_valid_inactive_keys = AsyncMock(return_value=[])
    token_manager.invalidate_key = Mock()
    token_manager.update_keydata = Mock()
    synced_manager.get_jwks = AsyncMock()
    synced_manager.set_jwks = AsyncMock()
    synced_manager.set_operation_cooldown = AsyncMock()
    lock_factory.lock = AsyncMock()
    announcer.stream_update = AsyncMock()

    manager = KeyLifecycleManager(
        repository,
        token_manager,
        key_config,  # type: ignore
        synced_manager,
        lock_factory,
        announcer,
    )
    return LifecycleHarness(
        manager,
        repository,
        token_manager,
        key_config,
        synced_manager,
        lock_factory,
        announcer,
        repository_context,
    )


@pytest.fixture
def bypass_transaction(lifecycle: LifecycleHarness) -> AsyncContext:
    context = make_async_context()
    object.__setattr__(
        lifecycle.manager, "_transactional_block", Mock(return_value=context)
    )
    return context


def test_generate_distributed_lock_name_uses_configured_components(
    lifecycle: LifecycleHarness,
) -> None:
    assert (
        lifecycle.manager.generate_distributed_lock_name("operation")
        == "LOCK:operation"
    )


def test_transactional_workers_have_stable_order(lifecycle: LifecycleHarness) -> None:
    assert lifecycle.manager._transactional_stack_order == (
        lifecycle.synced_manager,
        lifecycle.token_manager,
    )


@pytest.mark.asyncio
async def test_transactional_block_enters_lock_and_workers_then_cooldown_in_order(
    lifecycle: LifecycleHarness,
) -> None:
    calls = Mock()
    lock_state = SimpleNamespace(valid=True)
    lock_context = make_async_context(lock_state)
    synced_context = make_async_context()
    token_context = make_async_context()
    lifecycle.lock_factory.lock.return_value = lock_context
    lifecycle.synced_manager.transactional_block = Mock(return_value=synced_context)
    lifecycle.token_manager.transactional_block = Mock(return_value=token_context)

    calls.attach_mock(lifecycle.lock_factory.lock, "lock")
    calls.attach_mock(lock_context.enter, "lock_enter")
    calls.attach_mock(lock_context.exit, "lock_exit")
    calls.attach_mock(lifecycle.synced_manager.transactional_block, "synced_block")
    calls.attach_mock(synced_context.enter, "synced_enter")
    calls.attach_mock(synced_context.exit, "synced_exit")
    calls.attach_mock(lifecycle.token_manager.transactional_block, "token_block")
    calls.attach_mock(token_context.enter, "token_enter")
    calls.attach_mock(token_context.exit, "token_exit")
    calls.attach_mock(
        lifecycle.synced_manager.set_operation_cooldown,
        "cooldown",
    )
    body = Mock()
    calls.attach_mock(body, "body")

    async with lifecycle.manager._transactional_block(
        operation=SyncedStoreKeyStrings.JWKS_WRITE_LOCK,
        lock_duration=LockTIme.MEDIUM,
        operational_cooldown_duration=30,
    ):
        body()

    assert calls.mock_calls == [
        call.lock(
            lifecycle.manager.generate_distributed_lock_name(
                SyncedStoreKeyStrings.JWKS_WRITE_LOCK
            ),
            lifecycle.manager._lock_value,
            LockTIme.MEDIUM,
        ),
        call.lock_enter(),
        call.synced_block(),
        call.synced_enter(),
        call.token_block(),
        call.token_enter(),
        call.body(),
        call.cooldown(30),
        call.token_exit(None, None, None),
        call.synced_exit(None, None, None),
        call.lock_exit(None, None, None),
    ]


@pytest.mark.asyncio
async def test_transactional_block_without_operation_skips_lock_and_cooldown(
    lifecycle: LifecycleHarness,
) -> None:
    synced_context = make_async_context()
    token_context = make_async_context()
    lifecycle.synced_manager.transactional_block = Mock(return_value=synced_context)
    lifecycle.token_manager.transactional_block = Mock(return_value=token_context)

    async with lifecycle.manager._transactional_block():
        pass

    lifecycle.lock_factory.lock.assert_not_awaited()
    lifecycle.synced_manager.set_operation_cooldown.assert_not_awaited()
    synced_context.enter.assert_awaited_once_with()
    token_context.enter.assert_awaited_once_with()
    token_context.exit.assert_awaited_once_with(None, None, None)
    synced_context.exit.assert_awaited_once_with(None, None, None)


@pytest.mark.asyncio
async def test_transactional_block_rejects_failed_lock_before_workers(
    lifecycle: LifecycleHarness,
) -> None:
    lock_context = make_async_context(SimpleNamespace(valid=False))
    lifecycle.lock_factory.lock.return_value = lock_context
    lifecycle.synced_manager.transactional_block = Mock()
    lifecycle.token_manager.transactional_block = Mock()

    with pytest.raises(Exception, match="Failed to acquire operational lock"):
        async with lifecycle.manager._transactional_block(
            operation=SyncedStoreKeyStrings.JWKS_WRITE_LOCK
        ):
            pass

    lifecycle.synced_manager.transactional_block.assert_not_called()
    lifecycle.token_manager.transactional_block.assert_not_called()
    lock_context.exit.assert_awaited_once()


@pytest.mark.asyncio
async def test_transactional_block_skips_cooldown_and_unwinds_on_error(
    lifecycle: LifecycleHarness,
) -> None:
    synced_context = make_async_context()
    token_context = make_async_context()
    lifecycle.synced_manager.transactional_block = Mock(return_value=synced_context)
    lifecycle.token_manager.transactional_block = Mock(return_value=token_context)

    with pytest.raises(RuntimeError, match="failed"):
        async with lifecycle.manager._transactional_block(
            operational_cooldown_duration=30
        ):
            raise RuntimeError("failed")

    lifecycle.synced_manager.set_operation_cooldown.assert_not_awaited()
    token_context.exit.assert_awaited_once()
    synced_context.exit.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalidate_key_rejects_missing_key(
    lifecycle: LifecycleHarness,
    bypass_transaction: AsyncContext,
) -> None:
    lifecycle.repository.get_keydata.return_value = None

    with pytest.raises(ValueError, match="No key with ID missing found"):
        await lifecycle.manager.invalidate_key("missing")

    lifecycle.repository.get_keydata.assert_awaited_once_with(
        "missing",
        public_only=False,
        lock_args=(SelectionLockOption.KEY_SHARE, SelectionLockOption.READ),
    )
    lifecycle.repository.expire_keydata.assert_not_awaited()


@pytest.mark.parametrize(
    ("rotated_out_at", "expired_at", "message"),
    (
        (None, None, "Cannot invalidate active key key-1"),
        (
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            "Key key-1 has already been expired",
        ),
    ),
)
@pytest.mark.asyncio
async def test_invalidate_key_rejects_invalid_key_state(
    lifecycle: LifecycleHarness,
    bypass_transaction: AsyncContext,
    rotated_out_at: datetime | None,
    expired_at: datetime | None,
    message: str,
) -> None:
    lifecycle.repository.get_keydata.return_value = SimpleNamespace(
        kid="key-1",
        rotated_out_at=rotated_out_at,
        expired_at=expired_at,
    )

    with pytest.raises(Exception, match=message):
        await lifecycle.manager.invalidate_key("key-1")

    lifecycle.repository.expire_keydata.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalidate_key_prunes_consistent_synced_jwks(
    lifecycle: LifecycleHarness,
    bypass_transaction: AsyncContext,
) -> None:
    target = SimpleNamespace(
        kid="key-1",
        rotated_out_at=datetime(2026, 1, 1, tzinfo=UTC),
        expired_at=None,
    )
    target_jwk = SimpleNamespace(kid="key-1")
    retained_jwk = SimpleNamespace(kid="key-2")
    lifecycle.repository.get_keydata.return_value = target
    lifecycle.synced_manager.get_jwks.return_value = [target_jwk, retained_jwk]
    parsed_jwks = object()

    with patch(
        "auth_server.subsystems.key_manager.VariableJWKS.model_validate",
        return_value=parsed_jwks,
    ) as model_validate:
        await lifecycle.manager.invalidate_key("key-1", cooldown_duration=30)

    lifecycle.repository.expire_keydata.assert_awaited_once_with("key-1")
    lifecycle.token_manager.invalidate_key.assert_called_once_with("key-1")
    lifecycle.synced_manager.set_jwks.assert_awaited_once_with([retained_jwk])
    model_validate.assert_called_once_with({"keys": [retained_jwk]})
    lifecycle.announcer.stream_update.assert_awaited_once_with(parsed_jwks)
    lifecycle.manager._transactional_block.assert_called_once_with(  # type: ignore[attr-defined]
        operation=SyncedStoreKeyStrings.JWKS_WRITE_LOCK,
        lock_duration=LockTIme.MEDIUM,
        operational_cooldown_duration=30,
    )


@pytest.mark.asyncio
async def test_invalidate_key_regenerates_inconsistent_jwks_and_sets_warning(
    lifecycle: LifecycleHarness,
    bypass_transaction: AsyncContext,
) -> None:
    lifecycle.repository.get_keydata.return_value = SimpleNamespace(
        kid="key-1",
        rotated_out_at=datetime(2026, 1, 1, tzinfo=UTC),
        expired_at=None,
    )
    lifecycle.synced_manager.get_jwks.return_value = [SimpleNamespace(kid="key-2")]
    regenerated = [SimpleNamespace(kid="key-2"), SimpleNamespace(kid="key-3")]
    lifecycle.repository.get_jwks.return_value = regenerated
    message_mapping: dict[str, str] = {}

    with patch(
        "auth_server.subsystems.key_manager.VariableJWKS.model_validate",
        return_value=object(),
    ):
        await lifecycle.manager.invalidate_key(
            "key-1",
            intermediate_message_mapping=message_mapping,
        )

    assert "keylist_integrity_warning" in message_mapping
    lifecycle.synced_manager.set_jwks.assert_awaited_once_with(regenerated)


@pytest.mark.asyncio
async def test_clean_keystore_rejects_state_without_inactive_keys(
    lifecycle: LifecycleHarness,
    bypass_transaction: AsyncContext,
) -> None:
    lifecycle.repository.get_jwks.return_value = [SimpleNamespace(kid="active")]

    with pytest.raises(Exception, match="No inactive keys present to invalidate"):
        await lifecycle.manager.clean_keystore()

    lifecycle.repository.batch_expire_keydata.assert_not_awaited()


@pytest.mark.asyncio
async def test_clean_keystore_expires_inactive_keys_and_announces_active_key(
    lifecycle: LifecycleHarness,
    bypass_transaction: AsyncContext,
) -> None:
    active = SimpleNamespace(kid="active")
    inactive = [SimpleNamespace(kid="old-1"), SimpleNamespace(kid="old-2")]
    lifecycle.repository.get_jwks.return_value = [active, *inactive]
    parsed_jwks = object()

    with patch(
        "auth_server.subsystems.key_manager.VariableJWKS.model_validate",
        return_value=parsed_jwks,
    ) as model_validate:
        result = await lifecycle.manager.clean_keystore(cooldown_duration=30)

    assert result == ("active", ("old-1", "old-2"))
    lifecycle.repository.batch_expire_keydata.assert_awaited_once_with(
        ("old-1", "old-2")
    )
    lifecycle.synced_manager.set_jwks.assert_awaited_once_with((active,))
    assert lifecycle.token_manager.invalidate_key.call_args_list == [
        call("old-1"),
        call("old-2"),
    ]
    model_validate.assert_called_once_with({"keys": [active]})
    lifecycle.announcer.stream_update.assert_awaited_once_with(parsed_jwks)
    lifecycle.manager._transactional_block.assert_called_once_with(  # type: ignore[attr-defined]
        operation=SyncedStoreKeyStrings.JWKS_WRITE_LOCK,
        lock_duration=LockTIme.MEDIUM,
        operational_cooldown_duration=30,
    )


@pytest.mark.asyncio
async def test_rotate_key_rejects_missing_active_key(
    lifecycle: LifecycleHarness,
    bypass_transaction: AsyncContext,
) -> None:
    lifecycle.repository.get_active_key.return_value = None

    with (
        patch(
            "auth_server.subsystems.key_manager.generate_ecdsa_pair",
            return_value=("new-key", object(), object()),
        ),
        patch(
            "auth_server.subsystems.key_manager.pem_serialize_private_key",
            return_value=b"private",
        ),
        patch(
            "auth_server.subsystems.key_manager.pem_serialize_public_key",
            return_value=b"public",
        ),
    ):
        with pytest.raises(Exception, match="Invalid key state"):
            await lifecycle.manager.rotate_key()

    lifecycle.repository.rotate_key.assert_not_awaited()


@pytest.mark.asyncio
async def test_rotate_key_updates_state_and_announces_jwks(
    lifecycle: LifecycleHarness,
    bypass_transaction: AsyncContext,
) -> None:
    previous = SimpleNamespace(kid="previous-key")
    public_copy = object()
    new_key = Mock(kid="new-key")
    new_key.create_public_copy.return_value = public_copy
    jwks = [SimpleNamespace(kid="new-key"), SimpleNamespace(kid="previous-key")]
    lifecycle.repository.get_active_key.return_value = previous
    lifecycle.repository.rotate_key.return_value = new_key
    lifecycle.repository.get_jwks.return_value = jwks
    generation_epoch = datetime(2026, 1, 2, tzinfo=UTC)
    parsed_jwks = object()

    with (
        patch(
            "auth_server.subsystems.key_manager.generate_ecdsa_pair",
            return_value=("new-key", "signing-key", "verification-key"),
        ),
        patch(
            "auth_server.subsystems.key_manager.pem_serialize_private_key",
            return_value=b"private-pem",
        ) as serialize_private,
        patch(
            "auth_server.subsystems.key_manager.pem_serialize_public_key",
            return_value=b"public-pem",
        ) as serialize_public,
        patch("auth_server.subsystems.key_manager.datetime") as datetime_mock,
        patch(
            "auth_server.subsystems.key_manager.VariableJWKS.model_validate",
            return_value=parsed_jwks,
        ) as model_validate,
    ):
        datetime_mock.now.return_value = generation_epoch
        result = await lifecycle.manager.rotate_key(
            cooldown_duration=30,
            rotation_author=7,
        )

    assert result is public_copy
    serialize_private.assert_called_once_with("signing-key", "private-format")
    serialize_public.assert_called_once_with("verification-key", "public-format")
    lifecycle.repository.get_active_key.assert_awaited_once_with(
        lock_args=(SelectionLockOption.READ, SelectionLockOption.KEY_SHARE)
    )
    lifecycle.repository.rotate_key.assert_awaited_once_with(
        "previous-key",
        "new-key",
        b"public-pem",
        b"private-pem",
        rotation_author=7,
        epoch=generation_epoch,
        returning=True,
        public_only=False,
    )
    lifecycle.token_manager.update_keydata.assert_called_once_with("new-key", new_key)
    lifecycle.synced_manager.set_jwks.assert_awaited_once_with(jwks)
    model_validate.assert_called_once_with({"keys": jwks})
    lifecycle.announcer.stream_update.assert_awaited_once_with(parsed_jwks)
    lifecycle.manager._transactional_block.assert_called_once_with(  # type: ignore[attr-defined]
        operation=SyncedStoreKeyStrings.JWKS_WRITE_LOCK,
        lock_duration=LockTIme.MEDIUM,
        operational_cooldown_duration=30,
    )


@pytest.mark.asyncio
async def test_rotate_key_expires_oldest_keys_beyond_capacity(
    lifecycle: LifecycleHarness,
    bypass_transaction: AsyncContext,
) -> None:
    lifecycle.repository.get_active_key.return_value = SimpleNamespace(kid="active")
    new_key = Mock(kid="new-key")
    lifecycle.repository.rotate_key.return_value = new_key
    lifecycle.repository.get_valid_inactive_keys.return_value = [
        SimpleNamespace(kid="newest", rotated_out_at=datetime(2026, 1, 3, tzinfo=UTC)),
        SimpleNamespace(kid="oldest", rotated_out_at=datetime(2026, 1, 1, tzinfo=UTC)),
        SimpleNamespace(kid="middle", rotated_out_at=datetime(2026, 1, 2, tzinfo=UTC)),
    ]
    lifecycle.repository.get_jwks.return_value = []

    with (
        patch(
            "auth_server.subsystems.key_manager.generate_ecdsa_pair",
            return_value=("new-key", object(), object()),
        ),
        patch(
            "auth_server.subsystems.key_manager.pem_serialize_private_key",
            return_value=b"private",
        ),
        patch(
            "auth_server.subsystems.key_manager.pem_serialize_public_key",
            return_value=b"public",
        ),
        patch(
            "auth_server.subsystems.key_manager.VariableJWKS.model_validate",
            return_value=object(),
        ),
    ):
        await lifecycle.manager.rotate_key()

    lifecycle.repository.batch_expire_keydata.assert_awaited_once_with(("oldest",))
