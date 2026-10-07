from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from auxillary.data_structures.locks.lock import (
    BasicLockContext,
    RedisInstanceLockFactory,
)


@pytest.mark.asyncio
async def test_valid_lock_context_releases_using_its_token() -> None:
    client = SimpleNamespace(release=AsyncMock())
    context = BasicLockContext("resource", "token", client, True)  # type: ignore[arg-type]

    async with context:
        pass

    client.release.assert_awaited_once_with("resource", "token")


@pytest.mark.asyncio
async def test_invalid_lock_context_does_not_release() -> None:
    client = SimpleNamespace(release=AsyncMock())
    context = BasicLockContext("resource", "token", client, False)  # type: ignore[arg-type]

    async with context:
        pass

    client.release.assert_not_awaited()


def make_lock_factory() -> tuple[RedisInstanceLockFactory, Mock]:
    redis_client = Mock()
    redis_client.register_script.return_value = AsyncMock()
    redis_client.set = AsyncMock()
    factory = RedisInstanceLockFactory(redis_client)
    object.__setattr__(factory, "_registrered_release_script", AsyncMock())
    return factory, redis_client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ttl_in_ms", "expiration_argument"),
    ((False, {"ex": 30}), (True, {"px": 30})),
)
async def test_lock_uses_requested_ttl_unit(
    ttl_in_ms: bool, expiration_argument: dict[str, int]
) -> None:
    factory, redis_client = make_lock_factory()
    redis_client.set.return_value = True

    context = await factory.lock("resource", "token", 30, ttl_in_ms=ttl_in_ms)

    redis_client.set.assert_awaited_once_with(
        "resource", "token", **expiration_argument, nx=True
    )
    assert context.resource == "resource"
    assert context.value == "token"
    assert context.valid is True


@pytest.mark.asyncio
async def test_release_runs_registered_conditional_script() -> None:
    factory, redis_client = make_lock_factory()

    await factory.release("resource", "token")
    factory._registrered_release_script.assert_awaited_once_with(  # type: ignore
        keys=["resource"], args=["token"]
    )
