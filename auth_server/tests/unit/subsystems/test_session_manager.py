from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Final
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

import pytest
from auth_server.admin.roles import AdminRole
from auth_server.config import AdminConfigModel
from auth_server.models.session import AdminSession
from auth_server.subsystems.session_manager import AdminSessionManager
from pydantic import ValidationError


@dataclass
class RedisHarness:
    store: Mock
    pipeline: Mock
    calls: Mock


@pytest.fixture
def admin_config() -> AdminConfigModel:
    return AdminConfigModel(
        SUSPICIOUS_LOOKBACK_TIME=300,
        MAX_ACTIVITY_LIMIT=10,
        MAX_SESSION_ITERATIONS=3,
        ADMIN_SESSION_DURATION=60,
        REVIVAL_DIGEST_LENGTH=32,
    )


@pytest.fixture
def redis_harness() -> RedisHarness:
    calls = Mock()
    store = Mock()
    pipeline = Mock()
    context = MagicMock()

    store.hgetall = AsyncMock()
    store.hget = AsyncMock()
    store.pipeline = Mock(return_value=context)
    pipeline.hset = Mock(return_value=pipeline)
    pipeline.hsetex = Mock(return_value=pipeline)
    pipeline.hdel = Mock(return_value=pipeline)
    pipeline.delete = Mock(return_value=pipeline)
    pipeline.execute = AsyncMock(return_value=[])
    context.__aenter__ = AsyncMock(return_value=pipeline)
    context.__aexit__ = AsyncMock(return_value=None)

    calls.attach_mock(store.hgetall, "hgetall")
    calls.attach_mock(store.hget, "hget")
    calls.attach_mock(store.pipeline, "pipeline")
    calls.attach_mock(context.__aenter__, "enter")
    calls.attach_mock(pipeline.hset, "hset")
    calls.attach_mock(pipeline.hsetex, "hsetex")
    calls.attach_mock(pipeline.hdel, "hdel")
    calls.attach_mock(pipeline.delete, "delete")
    calls.attach_mock(pipeline.execute, "execute")
    calls.attach_mock(context.__aexit__, "exit")

    return RedisHarness(store, pipeline, calls)


@pytest.fixture
def manager_factory(
    admin_config: AdminConfigModel,
) -> Callable[[Mock], AdminSessionManager]:
    def make_manager(store: Mock) -> AdminSessionManager:
        manager = object.__new__(AdminSessionManager)
        AdminSessionManager.__init__(manager, store, admin_config)  # type: ignore[arg-type]
        return manager

    return make_manager


@pytest.fixture
def manager(
    manager_factory: Callable[[Mock], AdminSessionManager],
    redis_harness: RedisHarness,
) -> AdminSessionManager:
    return manager_factory(redis_harness.store)


ADMIN_ID: Final[int] = 7
EXPIRY_TIMESTAMP: Final[int] = 200
EPOCH_TIMESTAMP: Final[int] = 100
ITERATION: Final[int] = 2
REVIVAL_DIGEST: Final[str] = "revival-digest"
SESSION_ID: Final[str] = "session-id"


@pytest.fixture
def session() -> AdminSession:
    return AdminSession(
        session_id=SESSION_ID,
        admin_id=ADMIN_ID,
        expiry_timestamp=EXPIRY_TIMESTAMP,
        revival_digest=REVIVAL_DIGEST,
        epoch_timestamp=EPOCH_TIMESTAMP,
        role=AdminRole.SUPER,
        iteration=ITERATION,
    )


def test_b64encode_removes_padding(manager: AdminSessionManager) -> None:
    assert manager.b64encode(b"test") == "dGVzdA"


@pytest.mark.parametrize("missing_ok", (True, False))
@pytest.mark.asyncio
async def test_get_admin_session_handles_missing_session(
    manager: AdminSessionManager,
    redis_harness: RedisHarness,
    missing_ok: bool,
) -> None:
    redis_harness.store.hgetall.return_value = {}

    if missing_ok:
        assert await manager.get_admin_session("missing", missing_ok=True) is None
    else:
        with pytest.raises(ValueError, match="No session found with ID: missing"):
            await manager.get_admin_session("missing", missing_ok=False)

    assert redis_harness.calls.mock_calls == [call.hgetall("admin.missing")]


@pytest.mark.asyncio
async def test_get_admin_session_validates_stored_mapping(
    manager: AdminSessionManager,
    redis_harness: RedisHarness,
    session: AdminSession,
) -> None:
    stored_mapping = session.model_dump_redis()
    redis_harness.store.hgetall.return_value = stored_mapping

    with patch.object(
        AdminSession,
        "model_validate",
        return_value=session,
    ) as model_validate:
        assert await manager.get_admin_session(SESSION_ID) is session

    model_validate.assert_called_once_with(stored_mapping)
    assert redis_harness.calls.mock_calls == [call.hgetall("admin.session-id")]


@pytest.mark.asyncio
async def test_get_admin_session_terminates_malformed_session(
    manager: AdminSessionManager,
    redis_harness: RedisHarness,
) -> None:
    stored_mapping = {"admin_id": ADMIN_ID, "malformed": "value"}
    redis_harness.store.hgetall.return_value = stored_mapping
    session_name: Final[str] = manager.generate_admin_session_name(SESSION_ID)
    validation_error = ValidationError.from_exception_data("AdminSession", [])

    with (
        patch.object(
            AdminSession,
            "model_validate",
            side_effect=validation_error,
        ),
        patch.object(
            AdminSessionManager,
            "terminate_malformed_session",
            new_callable=AsyncMock,
        ) as terminate,
    ):
        with pytest.raises(ValueError, match="Invalid session, please login again"):
            await manager.get_admin_session(SESSION_ID)

    terminate.assert_awaited_once_with(session_name, admin_id=ADMIN_ID)
    assert redis_harness.calls.mock_calls == [call.hgetall(session_name)]


@pytest.mark.parametrize("missing_ok", (True, False))
@pytest.mark.asyncio
async def test_get_admin_session_via_admin_id_handles_missing_mapping(
    manager: AdminSessionManager,
    redis_harness: RedisHarness,
    missing_ok: bool,
) -> None:
    redis_harness.store.hget.return_value = None

    if missing_ok:
        assert (
            await manager.get_admin_session_via_admin_id(ADMIN_ID, missing_ok=True)
            is None
        )
    else:
        with pytest.raises(
            ValueError,
            match=f"No session found for admin with ID: {ADMIN_ID}",
        ):
            await manager.get_admin_session_via_admin_id(ADMIN_ID, missing_ok=False)

    assert redis_harness.calls.mock_calls == [call.hget("active_admins", str(ADMIN_ID))]


@pytest.mark.parametrize("missing_ok", (True, False))
@pytest.mark.asyncio
async def test_get_admin_session_via_admin_id_delegates_session_lookup(
    manager: AdminSessionManager,
    redis_harness: RedisHarness,
    session: AdminSession,
    missing_ok: bool,
) -> None:
    redis_harness.store.hget.return_value = SESSION_ID

    with patch.object(
        AdminSessionManager,
        "get_admin_session",
        new_callable=AsyncMock,
        return_value=session,
    ) as get_session:
        assert (
            await manager.get_admin_session_via_admin_id(
                ADMIN_ID, missing_ok=missing_ok
            )
            is session
        )

    assert redis_harness.calls.mock_calls == [call.hget("active_admins", str(ADMIN_ID))]
    get_session.assert_awaited_once_with(SESSION_ID, missing_ok=missing_ok)


def test_derive_session_expiry_uses_configured_duration(
    manager: AdminSessionManager,
) -> None:
    assert manager.derive_session_expiry(EPOCH_TIMESTAMP) == 160


def test_generate_session_revival_digest_uses_configured_length(
    manager: AdminSessionManager,
) -> None:
    with patch(
        "auth_server.subsystems.session_manager.secrets.token_hex",
        return_value="digest",
    ) as token_hex:
        assert manager.generate_session_revival_digest() == "digest"

    token_hex.assert_called_once_with(32)


def test_generate_session_id_uses_identifier_byte_length(
    manager: AdminSessionManager,
) -> None:
    with patch(
        "auth_server.subsystems.session_manager.secrets.token_urlsafe",
        return_value=SESSION_ID,
    ) as token_urlsafe:
        assert manager.generate_session_id() == SESSION_ID

    token_urlsafe.assert_called_once_with(16)


@pytest.mark.asyncio
async def test_register_admin_session_queues_initial_registration_in_order(
    manager: AdminSessionManager,
    redis_harness: RedisHarness,
    session: AdminSession,
) -> None:
    assert await manager._register_admin_session(session) == (
        SESSION_ID,
        REVIVAL_DIGEST,
    )

    assert redis_harness.calls.mock_calls == [
        call.pipeline(transaction=True),
        call.enter(),
        call.hset("admin.session-id", mapping=session.model_dump_redis()),
        call.hsetex(
            "active_admins",
            str(ADMIN_ID),
            SESSION_ID,
            ex=timedelta(seconds=180),
        ),
        call.execute(),
        call.exit(None, None, None),
    ]


@pytest.mark.asyncio
async def test_register_admin_session_replaces_preceding_session_in_order(
    manager: AdminSessionManager,
    redis_harness: RedisHarness,
    session: AdminSession,
) -> None:
    await manager._register_admin_session(
        session,
        preceding_session_id="preceding-id",
    )

    assert redis_harness.calls.mock_calls == [
        call.pipeline(transaction=True),
        call.enter(),
        call.hset("admin.session-id", mapping=session.model_dump_redis()),
        call.hsetex(
            "active_admins",
            str(ADMIN_ID),
            SESSION_ID,
            ex=timedelta(seconds=180),
        ),
        call.hdel(manager.admin_session_reverse_mapping_name, str(session.admin_id)),
        call.delete("admin.preceding-id"),
        call.execute(),
        call.exit(None, None, None),
    ]


@pytest.mark.asyncio
async def test_initialize_session_builds_and_registers_session_in_order(
    manager: AdminSessionManager,
) -> None:
    calls = Mock()
    monotonic = Mock(return_value=100.9)
    generate_id = Mock(return_value="new-session-id")
    derive_expiry = Mock(return_value=160)
    generate_digest = Mock(return_value="new-digest")
    register = AsyncMock(return_value=("new-session-id", "new-digest"))
    calls.attach_mock(monotonic, "monotonic")
    calls.attach_mock(generate_id, "generate_id")
    calls.attach_mock(derive_expiry, "derive_expiry")
    calls.attach_mock(generate_digest, "generate_digest")
    calls.attach_mock(register, "register")

    with (
        patch("auth_server.subsystems.session_manager.time.monotonic", monotonic),
        patch.object(AdminSessionManager, "generate_session_id", generate_id),
        patch.object(AdminSessionManager, "derive_session_expiry", derive_expiry),
        patch.object(
            AdminSessionManager,
            "generate_session_revival_digest",
            generate_digest,
        ),
        patch.object(AdminSessionManager, "_register_admin_session", register),
    ):
        result = await manager.initialize_session(ADMIN_ID, AdminRole.SUPER)

    assert result == ("new-session-id", "new-digest")
    assert calls.mock_calls[:4] == [
        call.monotonic(),
        call.generate_id(),
        call.derive_expiry(100),
        call.generate_digest(),
    ]
    registered_session = calls.mock_calls[4].args[0]
    assert calls.mock_calls[4] == call.register(registered_session)
    assert registered_session == AdminSession(
        session_id="new-session-id",
        admin_id=ADMIN_ID,
        epoch_timestamp=100,
        expiry_timestamp=160,
        revival_digest="new-digest",
        role=AdminRole.SUPER,
    )


@pytest.mark.asyncio
async def test_refresh_session_builds_and_registers_successor_in_order(
    manager: AdminSessionManager,
    session: AdminSession,
) -> None:
    calls = Mock()
    successor = AdminSession.construct_session_successor(
        session,
        "next-session-id",
        300,
        360,
        "next-digest",
    )
    monotonic = Mock(return_value=300.8)
    generate_id = Mock(return_value="next-session-id")
    derive_expiry = Mock(return_value=360)
    generate_digest = Mock(return_value="next-digest")
    construct_successor = Mock(return_value=successor)
    register = AsyncMock(return_value=("next-session-id", "next-digest"))
    calls.attach_mock(monotonic, "monotonic")
    calls.attach_mock(generate_id, "generate_id")
    calls.attach_mock(derive_expiry, "derive_expiry")
    calls.attach_mock(generate_digest, "generate_digest")
    calls.attach_mock(construct_successor, "construct_successor")
    calls.attach_mock(register, "register")

    with (
        patch("auth_server.subsystems.session_manager.time.monotonic", monotonic),
        patch.object(AdminSessionManager, "generate_session_id", generate_id),
        patch.object(AdminSessionManager, "derive_session_expiry", derive_expiry),
        patch.object(
            AdminSessionManager,
            "generate_session_revival_digest",
            generate_digest,
        ),
        patch.object(
            AdminSession,
            "construct_session_successor",
            construct_successor,
        ),
        patch.object(AdminSessionManager, "_register_admin_session", register),
    ):
        result = await manager.refresh_session(session)

    assert result == ("next-session-id", "next-digest")
    assert calls.mock_calls == [
        call.monotonic(),
        call.generate_id(),
        call.derive_expiry(300),
        call.generate_digest(),
        call.construct_successor(
            session,
            "next-session-id",
            300,
            360,
            "next-digest",
        ),
        call.register(successor, preceding_session_id=SESSION_ID),
    ]


@pytest.mark.asyncio
async def test_terminate_session_queues_deletions_in_order(
    manager: AdminSessionManager,
    redis_harness: RedisHarness,
) -> None:
    await manager.terminate_session(SESSION_ID, ADMIN_ID)

    assert redis_harness.calls.mock_calls == [
        call.pipeline(),
        call.enter(),
        call.delete("admin.session-id"),
        call.hdel("active_admins", str(ADMIN_ID)),
        call.execute(),
        call.exit(None, None, None),
    ]


@pytest.mark.parametrize("admin_id", (None, ADMIN_ID))
@pytest.mark.asyncio
async def test_terminate_malformed_session_queues_available_deletions_in_order(
    manager: AdminSessionManager,
    redis_harness: RedisHarness,
    admin_id: int | None,
) -> None:
    session_name: Final[str] = manager.generate_admin_session_name(SESSION_ID)
    await manager.terminate_malformed_session(session_name, admin_id=admin_id)

    expected_calls = [
        call.pipeline(),
        call.enter(),
        call.delete("admin.session-id"),
    ]
    if admin_id is not None:
        expected_calls.append(call.hdel("active_admins", str(ADMIN_ID)))
    expected_calls.extend((call.execute(), call.exit(None, None, None)))
    assert redis_harness.calls.mock_calls == expected_calls


@pytest.mark.asyncio
async def test_terminate_session_via_object_delegates_identifiers(
    manager: AdminSessionManager,
    session: AdminSession,
) -> None:
    with patch.object(
        AdminSessionManager,
        "terminate_session",
        new_callable=AsyncMock,
    ) as terminate:
        await manager.terminate_session_via_object(session)

    terminate.assert_awaited_once_with(SESSION_ID, ADMIN_ID)


@pytest.mark.parametrize(
    ("delimiter", "prefix", "expected"),
    ((".", "admin", "admin.session-id"), (":", "session", "session:session-id")),
)
def test_generate_admin_session_name_uses_configured_components(
    manager_factory: Callable[[Mock], AdminSessionManager],
    redis_harness: RedisHarness,
    delimiter: str,
    prefix: str,
    expected: str,
) -> None:
    manager = manager_factory(redis_harness.store)
    object.__setattr__(manager, "session_delimiter_symbol", delimiter)
    object.__setattr__(manager, "admin_session_name_prefix", prefix)

    assert manager.generate_admin_session_name(SESSION_ID) == expected
