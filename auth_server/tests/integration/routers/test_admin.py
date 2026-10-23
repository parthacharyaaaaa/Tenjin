from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Final

import httpx
import pytest
import pytest_asyncio
from auth_server.admin.roles import AdminRole
from auth_server.dependencies import local
from auth_server.models.database import Admin, SuspiciousActivity
from auth_server.repositories.admin import AdminPublicResult, AdminRepository
from auth_server.repositories.suspicious_activity import SuspiciousActivityRepository
from auth_server.strings import AdminStrings
from auth_server.subsystems.session_manager import AdminSessionManager
from auxillary.data_structures.uow import MultiRepositoryWorkCoordinator
from auxillary.security.hashing import bcrypt_check_password, bcrypt_hash_password
from auxillary.utils import json_repr
from fastapi import FastAPI
from redis.asyncio.client import Redis
from sqlalchemy import delete, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

ADMIN_ROOT: Final[str] = "/cmd/admins"
ADMIN_PASSWORD: Final[str] = "correct-password"
STAFF_USERNAME: Final[str] = "staff_admin"
INITIAL_LAST_LOGIN: Final[datetime] = datetime(2020, 1, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def admin_password_hash() -> bytes:
    return bcrypt_hash_password(ADMIN_PASSWORD, salt_generation_rounds=4)


@pytest_asyncio.fixture(autouse=True)
async def prepare_admin_test_state(
    prepare_database_state: None,
    auth_app: FastAPI,
    dummy_app_config: SimpleNamespace,
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
    async_redis_client: Redis,
    test_superuser_admin_id: int,
    test_superuser_admin_name: str,
    admin_password_hash: bytes,
) -> AsyncIterator[None]:
    await async_redis_client.flushall()
    async with pg_async_sessionmaker() as session:
        await session.execute(delete(SuspiciousActivity))
        await session.execute(delete(Admin).where(Admin.id_ != test_superuser_admin_id))
        await session.execute(
            update(Admin)
            .where(Admin.id_ == test_superuser_admin_id)
            .values(
                username=test_superuser_admin_name,
                role=AdminRole.SUPER,
                password_hash=admin_password_hash,
                time_deleted=None,
                last_login=INITIAL_LAST_LOGIN,
                locked=False,
                created_by=test_superuser_admin_id,
            )
        )
        await session.execute(
            text("""
                SELECT setval(
                    pg_get_serial_sequence('admins', 'id_'),
                    COALESCE((SELECT MAX(id_) FROM admins), 1),
                    EXISTS (SELECT 1 FROM admins)
                )
            """)
        )
        await session.commit()

    auth_app.dependency_overrides[local.get_app_config] = lambda: dummy_app_config
    auth_app.dependency_overrides[local.get_repository_work_coordinator] = lambda: (
        MultiRepositoryWorkCoordinator(pg_async_sessionmaker)
    )
    try:
        yield
    finally:
        await async_redis_client.flushall()


@pytest_asyncio.fixture
async def staff_admin(
    admin_repository: AdminRepository,
    admin_password_hash: bytes,
    test_superuser_admin_id: int,
) -> AdminPublicResult:
    return await admin_repository.create_admin(
        STAFF_USERNAME,
        admin_password_hash,
        test_superuser_admin_id,
        role=AdminRole.STAFF,
        returning=True,
    )


@pytest_asyncio.fixture
async def staff_admin_test_client(
    test_client: httpx.AsyncClient,
    staff_admin: AdminPublicResult,
    admin_session_manager: AdminSessionManager,
) -> AsyncIterator[httpx.AsyncClient]:
    session_id, _ = await admin_session_manager.initialize_session(
        staff_admin.id, AdminRole.STAFF
    )
    test_client.headers.update({AdminStrings.SESSION_TOKEN_HEADER: session_id})
    try:
        yield test_client
    finally:
        await admin_session_manager.terminate_session(session_id, staff_admin.id)


@pytest.mark.asyncio
async def test_admin_login_creates_session_and_updates_last_login(
    test_client: httpx.AsyncClient,
    admin_repository: AdminRepository,
    admin_session_manager: AdminSessionManager,
    test_superuser_admin_id: int,
    test_superuser_admin_name: str,
) -> None:
    response: Final[httpx.Response] = await test_client.post(
        f"{ADMIN_ROOT}/login",
        json={"identity": test_superuser_admin_name, "password": ADMIN_PASSWORD},
    )
    response_json: Final[dict[str, Any]] = response.json()
    session = await admin_session_manager.get_admin_session(
        response_json["session_token"], missing_ok=False
    )
    persisted_admin = await admin_repository.get_admin(test_superuser_admin_id)

    assert response.status_code == 200
    assert session.admin_id == test_superuser_admin_id
    assert session.role == AdminRole.SUPER
    assert session.revival_digest == response_json["revival_digest"]
    assert persisted_admin is not None
    assert persisted_admin.last_login > INITIAL_LAST_LOGIN
    assert response_json["_links"] == [
        {
            "href": f"{ADMIN_ROOT}/refresh",
            "rel": "refresh",
            "type": "POST",
        },
        {
            "href": f"{ADMIN_ROOT}/logout",
            "rel": "logout",
            "type": "PATCH",
        },
    ]


@pytest.mark.asyncio
async def test_repeated_login_rotates_existing_session_and_records_activity(
    test_client: httpx.AsyncClient,
    admin_session_manager: AdminSessionManager,
    suspicious_activity_repository: SuspiciousActivityRepository,
    test_superuser_admin_id: int,
    test_superuser_admin_name: str,
) -> None:
    payload = {"identity": test_superuser_admin_name, "password": ADMIN_PASSWORD}
    first_response = await test_client.post(f"{ADMIN_ROOT}/login", json=payload)
    second_response = await test_client.post(f"{ADMIN_ROOT}/login", json=payload)
    first_session_id: Final[str] = first_response.json()["session_token"]
    second_session_id: Final[str] = second_response.json()["session_token"]
    second_session = await admin_session_manager.get_admin_session(
        second_session_id, missing_ok=False
    )
    activities = await suspicious_activity_repository.get_activity_log(
        test_superuser_admin_id
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_session_id != first_session_id
    assert second_session.iteration == 2
    assert await admin_session_manager.get_admin_session(first_session_id) is None
    assert [activity.description for activity in activities] == [
        "Session already active"
    ]


@pytest.mark.asyncio
async def test_login_rejects_unknown_admin(
    test_client: httpx.AsyncClient,
) -> None:
    response = await test_client.post(
        f"{ADMIN_ROOT}/login",
        json={"identity": "missing_admin", "password": ADMIN_PASSWORD},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "No admin with identity missing_admin found"}


@pytest.mark.asyncio
async def test_login_rejects_deleted_admin(
    test_client: httpx.AsyncClient,
    staff_admin: AdminPublicResult,
    admin_repository: AdminRepository,
) -> None:
    await admin_repository.delete_admin(staff_admin.id)

    response = await test_client.post(
        f"{ADMIN_ROOT}/login",
        json={"identity": staff_admin.username, "password": ADMIN_PASSWORD},
    )

    assert response.status_code == 410
    assert response.json() == {
        "detail": f"Admin {staff_admin.username} has been deleted"
    }


@pytest.mark.asyncio
async def test_login_rejects_locked_admin_and_records_activity(
    test_client: httpx.AsyncClient,
    staff_admin: AdminPublicResult,
    admin_repository: AdminRepository,
    suspicious_activity_repository: SuspiciousActivityRepository,
) -> None:
    await admin_repository.set_admin_locked(staff_admin.id, True)

    response = await test_client.post(
        f"{ADMIN_ROOT}/login",
        json={"identity": staff_admin.username, "password": ADMIN_PASSWORD},
    )
    activities = await suspicious_activity_repository.get_activity_log(staff_admin.id)

    assert response.status_code == 403
    assert response.json() == {
        "detail": (
            "This account is currently locked on grounds of suspicious activities"
        )
    }
    assert [activity.description for activity in activities] == [
        "Attempt to log into a locked account"
    ]


@pytest.mark.asyncio
async def test_login_rejects_incorrect_password_without_updating_last_login(
    test_client: httpx.AsyncClient,
    admin_repository: AdminRepository,
    suspicious_activity_repository: SuspiciousActivityRepository,
    test_superuser_admin_id: int,
    test_superuser_admin_name: str,
) -> None:
    response = await test_client.post(
        f"{ADMIN_ROOT}/login",
        json={"identity": test_superuser_admin_name, "password": "incorrect-password"},
    )
    persisted_admin = await admin_repository.get_admin(test_superuser_admin_id)
    activities = await suspicious_activity_repository.get_activity_log(
        test_superuser_admin_id
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Incorrect passwword"}
    assert persisted_admin is not None
    assert persisted_admin.last_login == INITIAL_LAST_LOGIN
    assert [activity.description for activity in activities] == ["Incorrect password"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    (
        {"identity": "admin' OR '1'='1", "password": ADMIN_PASSWORD},
        {"identity": "../TEST_ADMIN", "password": ADMIN_PASSWORD},
        {"identity": "TEST_ADMIN", "password": "short"},
    ),
)
async def test_login_rejects_malformed_or_injection_like_payloads(
    test_client: httpx.AsyncClient,
    suspicious_activity_repository: SuspiciousActivityRepository,
    test_superuser_admin_id: int,
    payload: dict[str, str],
) -> None:
    response = await test_client.post(f"{ADMIN_ROOT}/login", json=payload)

    assert response.status_code == 422
    assert (
        await suspicious_activity_repository.get_activity_log(test_superuser_admin_id)
        == []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    (
        ("DELETE", f"{ADMIN_ROOT}/1", None),
        ("POST", f"{ADMIN_ROOT}/refresh", {"refresh-digest": "digest"}),
        ("PATCH", f"{ADMIN_ROOT}/logout", None),
        ("POST", f"{ADMIN_ROOT}/locks/lock", {"id": 1}),
        ("DELETE", f"{ADMIN_ROOT}/locks/unlock", {"id": 1}),
        (
            "POST",
            f"{ADMIN_ROOT}/",
            {"identity": "new_admin", "password": ADMIN_PASSWORD},
        ),
    ),
)
async def test_protected_admin_routes_require_a_session(
    test_client: httpx.AsyncClient,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
) -> None:
    response = await test_client.request(method, path, json=payload)

    assert response.status_code == 401
    assert response.json()["detail"] == (
        f"Missing session token: {AdminStrings.SESSION_TOKEN_HEADER}"
    )


@pytest.mark.asyncio
async def test_super_admin_can_create_staff_admin(
    super_admin_test_client: httpx.AsyncClient,
    admin_repository: AdminRepository,
    test_superuser_admin_id: int,
) -> None:
    response = await super_admin_test_client.post(
        f"{ADMIN_ROOT}/",
        json={"identity": "created_admin", "password": ADMIN_PASSWORD},
    )
    created_admin_result = await admin_repository.get_admin_by_username(
        "created_admin", include_password_hash=True
    )

    assert response.status_code == 202
    assert created_admin_result is not None
    created_admin, password_hash = created_admin_result
    assert created_admin.role == AdminRole.STAFF
    assert created_admin.created_by == test_superuser_admin_id
    assert bcrypt_check_password(ADMIN_PASSWORD, password_hash)
    assert response.json()["admin"] == json_repr(created_admin)
    assert "password_hash" not in response.json()["admin"]


@pytest.mark.asyncio
async def test_create_admin_rejects_duplicate_username(
    super_admin_test_client: httpx.AsyncClient,
    staff_admin: AdminPublicResult,
) -> None:
    response = await super_admin_test_client.post(
        f"{ADMIN_ROOT}/",
        json={"identity": staff_admin.username, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 409
    assert response.json() == {
        "detail": f"Admin with username {staff_admin.username} already exists"
    }


@pytest.mark.asyncio
async def test_staff_cannot_create_admin(
    staff_admin_test_client: httpx.AsyncClient,
) -> None:
    response = await staff_admin_test_client.post(
        f"{ADMIN_ROOT}/",
        json={"identity": "forbidden_admin", "password": ADMIN_PASSWORD},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Missing permissions for: create_admin"}


@pytest.mark.asyncio
async def test_super_admin_can_delete_staff_admin(
    super_admin_test_client: httpx.AsyncClient,
    staff_admin: AdminPublicResult,
    admin_repository: AdminRepository,
) -> None:
    response = await super_admin_test_client.delete(f"{ADMIN_ROOT}/{staff_admin.id}")
    deleted_admin = await admin_repository.get_admin(
        staff_admin.id, include_deleted=True
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Admin deleted"
    assert response.json()["admin_id"] == staff_admin.id
    assert response.json()["admin_username"] == staff_admin.username
    assert deleted_admin is not None
    assert deleted_admin.time_deleted is not None
    assert response.json()["time_deleted"] == deleted_admin.time_deleted.isoformat()


@pytest.mark.asyncio
async def test_delete_admin_rejects_missing_admin(
    super_admin_test_client: httpx.AsyncClient,
) -> None:
    response = await super_admin_test_client.delete(f"{ADMIN_ROOT}/999999")

    assert response.status_code == 404
    assert response.json() == {"detail": "No admin with ID 999999 found"}


@pytest.mark.asyncio
async def test_delete_admin_rejects_already_deleted_admin(
    super_admin_test_client: httpx.AsyncClient,
    staff_admin: AdminPublicResult,
    admin_repository: AdminRepository,
) -> None:
    await admin_repository.delete_admin(staff_admin.id)

    response = await super_admin_test_client.delete(f"{ADMIN_ROOT}/{staff_admin.id}")

    assert response.status_code == 410
    assert response.json() == {
        "detail": (
            f"Admin {staff_admin.username} (ID: {staff_admin.id}) already deleted"
        )
    }


@pytest.mark.asyncio
async def test_staff_cannot_delete_admin(
    staff_admin_test_client: httpx.AsyncClient,
    test_superuser_admin_id: int,
) -> None:
    response = await staff_admin_test_client.delete(
        f"{ADMIN_ROOT}/{test_superuser_admin_id}"
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Missing permissions for: delete_admin"}


@pytest.mark.asyncio
async def test_refresh_rotates_session_and_returns_new_credentials(
    test_client: httpx.AsyncClient,
    admin_session_manager: AdminSessionManager,
    test_superuser_admin_id: int,
) -> None:
    old_session_id, old_digest = await admin_session_manager.initialize_session(
        test_superuser_admin_id, AdminRole.SUPER
    )
    test_client.headers.update({AdminStrings.SESSION_TOKEN_HEADER: old_session_id})

    response = await test_client.post(
        f"{ADMIN_ROOT}/refresh",
        json={"refresh-digest": old_digest},
    )
    response_json: Final[dict[str, Any]] = response.json()
    new_session = await admin_session_manager.get_admin_session(
        response_json["session_token"], missing_ok=False
    )

    assert response.status_code == 200
    assert response_json["session_token"] != old_session_id
    assert response_json["revival_digest"] != old_digest
    assert await admin_session_manager.get_admin_session(old_session_id) is None
    assert new_session.iteration == 2
    assert response_json["_links"] == [
        {
            "href": f"{ADMIN_ROOT}/refresh",
            "rel": "refresh",
            "type": "POST",
        },
        {
            "href": f"{ADMIN_ROOT}/logout",
            "rel": "logout",
            "type": "PATCH",
        },
    ]


@pytest.mark.asyncio
async def test_invalid_refresh_digest_locks_admin_and_terminates_session(
    test_client: httpx.AsyncClient,
    admin_session_manager: AdminSessionManager,
    admin_repository: AdminRepository,
    suspicious_activity_repository: SuspiciousActivityRepository,
    test_superuser_admin_id: int,
) -> None:
    session_id, _ = await admin_session_manager.initialize_session(
        test_superuser_admin_id, AdminRole.SUPER
    )
    test_client.headers.update({AdminStrings.SESSION_TOKEN_HEADER: session_id})

    response = await test_client.post(
        f"{ADMIN_ROOT}/refresh",
        json={"refresh-digest": "invalid-digest"},
    )
    persisted_admin = await admin_repository.get_admin(test_superuser_admin_id)
    activities = await suspicious_activity_repository.get_activity_log(
        test_superuser_admin_id
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid revival digest provided"
    assert persisted_admin is not None
    assert persisted_admin.locked is True
    assert await admin_session_manager.get_admin_session(session_id) is None
    assert [activity.description for activity in activities] == [
        "Invalid session revival digest"
    ]


@pytest.mark.asyncio
async def test_refresh_rejects_session_at_iteration_limit(
    test_client: httpx.AsyncClient,
    admin_session_manager: AdminSessionManager,
    dummy_admin_config: Any,
    test_superuser_admin_id: int,
) -> None:
    session_id, digest = await admin_session_manager.initialize_session(
        test_superuser_admin_id, AdminRole.SUPER
    )
    session = await admin_session_manager.get_admin_session(
        session_id, missing_ok=False
    )
    while session.iteration < dummy_admin_config.MAX_SESSION_ITERATIONS:
        session_id, digest = await admin_session_manager.refresh_session(session)
        session = await admin_session_manager.get_admin_session(
            session_id, missing_ok=False
        )
    test_client.headers.update({AdminStrings.SESSION_TOKEN_HEADER: session_id})

    response = await test_client.post(
        f"{ADMIN_ROOT}/refresh",
        json={"refresh-digest": digest},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Maximum session reiterations reached, please reauthenticate to be assigned "
        "a fresh session"
    )
    assert await admin_session_manager.get_admin_session(session_id) == session


@pytest.mark.asyncio
async def test_logout_terminates_current_session(
    test_client: httpx.AsyncClient,
    admin_session_manager: AdminSessionManager,
    test_superuser_admin_id: int,
) -> None:
    session_id, _ = await admin_session_manager.initialize_session(
        test_superuser_admin_id, AdminRole.SUPER
    )
    test_client.headers.update({AdminStrings.SESSION_TOKEN_HEADER: session_id})

    response = await test_client.patch(f"{ADMIN_ROOT}/logout")

    assert response.status_code == 200
    assert response.json() == {"message": "Logout successful"}
    assert await admin_session_manager.get_admin_session(session_id) is None
    assert (
        await admin_session_manager.get_admin_session_via_admin_id(
            test_superuser_admin_id
        )
        is None
    )


@pytest.mark.asyncio
async def test_lock_admin_updates_database_and_terminates_target_session(
    super_admin_test_client: httpx.AsyncClient,
    staff_admin: AdminPublicResult,
    admin_repository: AdminRepository,
    admin_session_manager: AdminSessionManager,
) -> None:
    target_session_id, _ = await admin_session_manager.initialize_session(
        staff_admin.id, AdminRole.STAFF
    )

    response = await super_admin_test_client.post(
        f"{ADMIN_ROOT}/locks/lock", json={"username": staff_admin.username}
    )
    persisted_admin = await admin_repository.get_admin(staff_admin.id)

    assert response.status_code == 200
    assert response.json() == {"message": "Admin locked succesfully"}
    assert persisted_admin is not None
    assert persisted_admin.locked is True
    assert await admin_session_manager.get_admin_session(target_session_id) is None


@pytest.mark.asyncio
async def test_lock_admin_rejects_missing_admin(
    super_admin_test_client: httpx.AsyncClient,
) -> None:
    response = await super_admin_test_client.post(
        f"{ADMIN_ROOT}/locks/lock", json={"username": "__NOT_FOUND__"}
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Admin __NOT_FOUND__ could not be found"}


@pytest.mark.asyncio
async def test_lock_admin_rejects_already_locked_admin(
    super_admin_test_client: httpx.AsyncClient,
    staff_admin: AdminPublicResult,
    admin_repository: AdminRepository,
) -> None:
    await admin_repository.set_admin_locked(staff_admin.id, True)

    response = await super_admin_test_client.post(
        f"{ADMIN_ROOT}/locks/lock", json={"username": staff_admin.username}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Admin account is already locked"


@pytest.mark.asyncio
async def test_unlock_admin_updates_database(
    super_admin_test_client: httpx.AsyncClient,
    staff_admin: AdminPublicResult,
    admin_repository: AdminRepository,
) -> None:
    await admin_repository.set_admin_locked(staff_admin.id, True)

    response = await super_admin_test_client.request(
        "DELETE", f"{ADMIN_ROOT}/locks/unlock", json={"username": staff_admin.username}
    )
    persisted_admin = await admin_repository.get_admin(staff_admin.id)

    assert response.status_code == 200
    assert response.json() == {"message": "Admin unlocked succesfully"}
    assert persisted_admin is not None
    assert persisted_admin.locked is False


@pytest.mark.asyncio
async def test_unlock_admin_rejects_missing_admin(
    super_admin_test_client: httpx.AsyncClient,
) -> None:
    response = await super_admin_test_client.request(
        "DELETE", f"{ADMIN_ROOT}/locks/unlock", json={"username": "__NOT_FOUND__"}
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Admin __NOT_FOUND__ could not be found"}


@pytest.mark.asyncio
async def test_unlock_admin_rejects_already_unlocked_admin(
    super_admin_test_client: httpx.AsyncClient,
    staff_admin: AdminPublicResult,
) -> None:
    response = await super_admin_test_client.request(
        "DELETE", f"{ADMIN_ROOT}/locks/unlock", json={"username": staff_admin.username}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Admin account is already unlocked"
