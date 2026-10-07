from dataclasses import dataclass
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

import orjson
import pytest
from auth_server.repositories.keydata import (
    EllipticCurveJWKResult,
    KeyPublicDataResult,
)
from auth_server.strings import SyncedStoreKeyStrings
from auth_server.subsystems.key_manager import SyncedStoreKeyStateManager
from auxillary.security.data_structures.jwks.enums import ECAlg


@dataclass
class RedisHarness:
    store: Mock
    pipeline: Mock
    calls: Mock


@pytest.fixture
def redis_harness() -> RedisHarness:
    calls = Mock()
    store = Mock()
    pipeline = Mock()
    context = MagicMock()

    store.get = AsyncMock()
    store.set = AsyncMock()
    store.pipeline = Mock(return_value=context)
    pipeline.set = Mock(return_value=pipeline)
    pipeline.execute = AsyncMock(return_value=[])
    context.__aenter__ = AsyncMock(return_value=pipeline)
    context.__aexit__ = AsyncMock(return_value=None)

    calls.attach_mock(store.get, "get")
    calls.attach_mock(store.set, "set")
    calls.attach_mock(store.pipeline, "pipeline")
    calls.attach_mock(context.__aenter__, "enter")
    calls.attach_mock(pipeline.set, "pipeline_set")
    calls.attach_mock(pipeline.execute, "execute")
    calls.attach_mock(context.__aexit__, "exit")
    return RedisHarness(store, pipeline, calls)


@pytest.fixture
def manager(redis_harness: RedisHarness) -> SyncedStoreKeyStateManager:
    return SyncedStoreKeyStateManager(redis_harness.store)  # type: ignore[arg-type]


@pytest.fixture
def jwk() -> EllipticCurveJWKResult:
    return EllipticCurveJWKResult(
        alg=ECAlg.ES256,
        crv="secp256k1",
        kid="key-1",
        x="x-coordinate",
        y="y-coordinate",
    )


@pytest.mark.asyncio
async def test_transactional_block_reads_snapshot_without_writes_on_success(
    manager: SyncedStoreKeyStateManager,
    redis_harness: RedisHarness,
) -> None:
    redis_harness.store.get.return_value = b"original-jwks"

    async with manager.transactional_block():
        pass

    assert redis_harness.calls.mock_calls == [call.get(SyncedStoreKeyStrings.JWKS_KEY)]


@pytest.mark.asyncio
async def test_transactional_block_restores_snapshot_before_reraising(
    manager: SyncedStoreKeyStateManager,
    redis_harness: RedisHarness,
) -> None:
    redis_harness.store.get.return_value = b"original-jwks"

    with pytest.raises(RuntimeError, match="operation failed"):
        async with manager.transactional_block():
            raise RuntimeError("operation failed")

    assert redis_harness.calls.mock_calls == [
        call.get(SyncedStoreKeyStrings.JWKS_KEY),
        call.pipeline(transaction=True),
        call.enter(),
        call.pipeline_set(
            SyncedStoreKeyStrings.JWKS_KEY,
            b"original-jwks",
        ),
        call.execute(),
        call.exit(None, None, None),
    ]


@pytest.mark.parametrize(
    ("stored_value", "expected"),
    ((None, False), (b"", False), (0, False), (b"1", True), ("0", True)),
)
@pytest.mark.asyncio
async def test_get_key_operational_cooldown_reflects_redis_truthiness(
    manager: SyncedStoreKeyStateManager,
    redis_harness: RedisHarness,
    stored_value: object,
    expected: bool,
) -> None:
    redis_harness.store.get.return_value = stored_value

    assert await manager.get_key_operational_cooldown() is expected
    redis_harness.store.get.assert_awaited_once_with(
        SyncedStoreKeyStrings.KEY_ROTATION_COOLDOWN
    )


@pytest.mark.parametrize("cooldown", (15, timedelta(seconds=15)))
@pytest.mark.asyncio
async def test_set_operation_cooldown_uses_expiry(
    manager: SyncedStoreKeyStateManager,
    redis_harness: RedisHarness,
    cooldown: int | timedelta,
) -> None:
    await manager.set_operation_cooldown(cooldown)

    redis_harness.store.set.assert_awaited_once_with(
        SyncedStoreKeyStrings.KEY_ROTATION_COOLDOWN,
        1,
        ex=cooldown,
    )


@pytest.mark.asyncio
async def test_get_jwks_converts_each_cached_key(
    manager: SyncedStoreKeyStateManager,
    redis_harness: RedisHarness,
    jwk: EllipticCurveJWKResult,
) -> None:
    cached_keys = [{"kid": "key-1"}, {"kid": "key-2"}]
    public_results = [Mock(), Mock()]
    public_results[0].as_jwk.return_value = jwk
    second_jwk = EllipticCurveJWKResult(
        alg=ECAlg.ES256,
        crv="secp256k1",
        kid="key-2",
        x="x-2",
        y="y-2",
    )
    public_results[1].as_jwk.return_value = second_jwk
    redis_harness.store.get.return_value = orjson.dumps({"keys": cached_keys})

    with patch.object(
        KeyPublicDataResult,
        "construct_from_cache",
        side_effect=public_results,
    ) as construct:
        assert (await manager.get_jwks()) == [jwk, second_jwk]

    assert construct.call_args_list == [call(cached_keys[0]), call(cached_keys[1])]


@pytest.mark.asyncio
async def test_set_jwks_serializes_jwk_results(
    manager: SyncedStoreKeyStateManager,
    redis_harness: RedisHarness,
    jwk: EllipticCurveJWKResult,
) -> None:
    await manager.set_jwks((jwk,))

    redis_harness.store.set.assert_awaited_once_with(
        SyncedStoreKeyStrings.JWKS_KEY,
        orjson.dumps({"keys": [jwk.__json_repr__()]}),
    )


@pytest.mark.asyncio
async def test_set_jwks_converts_public_results_before_serializing(
    manager: SyncedStoreKeyStateManager,
    redis_harness: RedisHarness,
    jwk: EllipticCurveJWKResult,
) -> None:
    public_results = (
        object.__new__(KeyPublicDataResult),
        object.__new__(KeyPublicDataResult),
    )
    second_jwk = EllipticCurveJWKResult(
        alg=ECAlg.ES256,
        crv="secp256k1",
        kid="key-2",
        x="x-2",
        y="y-2",
    )

    with patch.object(
        KeyPublicDataResult,
        "as_jwk",
        side_effect=(jwk, second_jwk),
    ) as as_jwk:
        await manager.set_jwks(public_results)

    assert as_jwk.call_args_list == [
        call(public_results[0]),
        call(public_results[1]),
    ]
    redis_harness.store.set.assert_awaited_once_with(
        SyncedStoreKeyStrings.JWKS_KEY,
        orjson.dumps({"keys": [jwk.__json_repr__(), second_jwk.__json_repr__()]}),
    )


@pytest.mark.asyncio
async def test_set_jwks_rejects_empty_key_sequence(
    manager: SyncedStoreKeyStateManager,
    redis_harness: RedisHarness,
) -> None:
    with pytest.raises(ValueError, match="keys must be non-empty"):
        await manager.set_jwks(())
