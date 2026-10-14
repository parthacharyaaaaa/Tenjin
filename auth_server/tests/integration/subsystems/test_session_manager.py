from unittest.mock import patch

import pytest
from auth_server.admin.roles import AdminRole
from auth_server.models.session import AdminSession
from auth_server.subsystems.session_manager import AdminSessionManager
from redis.asyncio.client import Redis
from sqlalchemy.util.typing import Final


async def assert_session_non_existence(
    async_redis_client: Redis,
    admin_session_manager: AdminSessionManager,
    session_id: str,
    admin_id: int,
) -> None:
    assert await admin_session_manager.get_admin_session(session_id) is None
    assert (
        await async_redis_client.hget(
            admin_session_manager.admin_session_reverse_mapping_name, admin_id
        )
        is None
    )  # type: ignore


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_ok", (True, False))
async def test_get_admin_session_missing_behaviour(
    admin_session_manager: AdminSessionManager, missing_ok: bool
) -> None:
    session_id: Final[str] = "__NOT_FOUND__"
    if missing_ok:
        assert (
            await admin_session_manager.get_admin_session(
                session_id, missing_ok=missing_ok
            )
            is None
        )
    else:
        with pytest.raises(ValueError, match=f"No session found with ID: {session_id}"):
            await admin_session_manager.get_admin_session(
                session_id, missing_ok=missing_ok
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_ok", (True, False))
async def test_get_admin_session_via_id_missing_behaviour(
    admin_session_manager: AdminSessionManager, missing_ok: bool
) -> None:
    admin_id: Final[int] = 1
    if missing_ok:
        assert (
            await admin_session_manager.get_admin_session_via_admin_id(
                admin_id, missing_ok=missing_ok
            )
            is None
        )
    else:
        with pytest.raises(
            ValueError, match=f"No session found for admin with ID: {admin_id}"
        ):
            await admin_session_manager.get_admin_session_via_admin_id(
                admin_id, missing_ok=missing_ok
            )


@pytest.mark.asyncio
async def test_admin_session_initialization(
    admin_session_manager: AdminSessionManager, async_redis_client: Redis
) -> None:
    admin_id: int = 1
    patched_session_epoch_timestamp: Final[int] = 1
    patched_generated_session_id: Final[str] = "foo"
    patched_generated_revival_digest: Final[str] = (
        "x" * admin_session_manager.admin_config.REVIVAL_DIGEST_LENGTH
    )

    with (
        patch("time.monotonic", return_value=patched_session_epoch_timestamp),
        patch(
            "auth_server.subsystems.session_manager.AdminSessionManager.generate_session_id",
            return_value=patched_generated_session_id,
        ),
        patch(
            "auth_server.subsystems.session_manager.AdminSessionManager.generate_session_revival_digest",
            return_value=patched_generated_revival_digest,
        ),
    ):
        expected_admin_session: Final[AdminSession] = AdminSession(
            session_id=admin_session_manager.generate_session_id(),
            admin_id=admin_id,
            epoch_timestamp=patched_session_epoch_timestamp,
            expiry_timestamp=admin_session_manager.derive_session_expiry(
                patched_session_epoch_timestamp
            ),
            revival_digest=admin_session_manager.generate_session_revival_digest(),
            role=AdminRole.STAFF,
        )
        session_id, _revival_digest = await admin_session_manager.initialize_session(
            admin_id, AdminRole.STAFF
        )
    fetched_admin_session: Final[
        AdminSession | None
    ] = await admin_session_manager.get_admin_session(session_id)
    assert fetched_admin_session is not None
    assert (
        await async_redis_client.hget(
            admin_session_manager.admin_session_reverse_mapping_name, str(admin_id)
        )
        == session_id
    )

    assert expected_admin_session.model_dump() == fetched_admin_session.model_dump(), (
        "Fetched session does not match expected session"
    )


@pytest.mark.asyncio
async def test_session_refresh(
    admin_session_manager: AdminSessionManager, async_redis_client: Redis
) -> None:
    admin_id: int = 1
    patched_session_epoch_timestamp: Final[int] = 1
    patched_generated_session_id: Final[str] = "foo"
    patched_refreshed_generated_session_id: Final[str] = "bar"
    patched_generated_revival_digest: Final[str] = (
        "x" * admin_session_manager.admin_config.REVIVAL_DIGEST_LENGTH
    )

    with (
        patch("time.monotonic", return_value=patched_session_epoch_timestamp),
        patch(
            "auth_server.subsystems.session_manager.AdminSessionManager.generate_session_id",
            return_value=patched_generated_session_id,
        ),
        patch(
            "auth_server.subsystems.session_manager.AdminSessionManager.generate_session_revival_digest",
            return_value=patched_generated_revival_digest,
        ),
    ):
        initial_admin_session: Final[AdminSession] = AdminSession(
            session_id=admin_session_manager.generate_session_id(),
            admin_id=admin_id,
            epoch_timestamp=patched_session_epoch_timestamp,
            expiry_timestamp=admin_session_manager.derive_session_expiry(
                patched_session_epoch_timestamp
            ),
            revival_digest=admin_session_manager.generate_session_revival_digest(),
            role=AdminRole.STAFF,
        )
        session_id, _admin_id = await admin_session_manager.initialize_session(
            admin_id, AdminRole.STAFF
        )
        with (
            patch(
                "auth_server.subsystems.session_manager.AdminSessionManager.generate_session_id",
                return_value=patched_refreshed_generated_session_id,
            ),
        ):
            new_session_id, _new_admin_id = await admin_session_manager.refresh_session(
                initial_admin_session
            )
            refreshed_session: AdminSession = (
                await admin_session_manager.get_admin_session(
                    new_session_id, missing_ok=False
                )
            )
            assert (
                refreshed_session.model_dump()
                == initial_admin_session.model_dump()
                | {"iteration": 2, "session_id": patched_refreshed_generated_session_id}
            )
            assert (
                await async_redis_client.hget(
                    admin_session_manager.admin_session_reverse_mapping_name,
                    str(admin_id),
                )
                == new_session_id
            )  # pyrefly: ignore[not-async]


@pytest.mark.asyncio
async def test_session_termination(
    admin_session_manager: AdminSessionManager, async_redis_client: Redis
) -> None:
    admin_id: Final[int] = 1
    session_id, _admin_id = await admin_session_manager.initialize_session(
        admin_id, AdminRole.STAFF
    )
    await admin_session_manager.terminate_session(session_id, admin_id)
    await assert_session_non_existence(
        async_redis_client, admin_session_manager, session_id, admin_id
    )


@pytest.mark.asyncio
async def test_malformed_session_termination(
    admin_session_manager: AdminSessionManager, async_redis_client: Redis
) -> None:
    admin_id: Final[int] = 1
    session_id, _admin_id = await admin_session_manager.initialize_session(
        admin_id, AdminRole.STAFF
    )
    await admin_session_manager.terminate_session(session_id, admin_id)
    await assert_session_non_existence(
        async_redis_client, admin_session_manager, session_id, admin_id
    )
