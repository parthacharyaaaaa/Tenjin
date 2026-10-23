from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from auth_server.admin.roles import AdminRole
from auth_server.models.data_transport.keydata import (
    ActiveKeyPublicDataResult,
    ExpiredKeyDataResult,
    RotatedKeyDataResult,
)
from auth_server.repositories.keydata import KeydataRepository
from auth_server.strings import AdminStrings, SyncedStoreKeyStrings
from auth_server.subsystems.key_manager import SyncedStoreKeyStateManager
from auth_server.subsystems.session_manager import AdminSessionManager
from auxillary.utils import json_repr
from fastapi.testclient import TestClient
from redis.asyncio.client import Redis
from sqlalchemy.util.typing import Final


@pytest_asyncio.fixture(scope="function")
async def prep_db(prepare_database_state: None) -> None:
    return


@pytest_asyncio.fixture(scope="function", autouse=True)
async def pre_test_prepper(
    prepare_database_state: None,
    keydata_repository: KeydataRepository,
    synced_key_store_manager: SyncedStoreKeyStateManager,
    async_redis_client: Redis,
) -> AsyncIterator[None]:
    await synced_key_store_manager.set_jwks(await keydata_repository.get_jwks())
    try:
        yield
    finally:
        await async_redis_client.delete(SyncedStoreKeyStrings.JWKS_KEY)


@pytest_asyncio.fixture
async def valid_inactive_keys(
    keydata_repository: KeydataRepository,
) -> list[RotatedKeyDataResult]:
    return await keydata_repository.get_valid_inactive_keys()


@pytest_asyncio.fixture
async def staff_admin_test_client(
    test_client: httpx.AsyncClient,
    admin_session_manager: AdminSessionManager,
) -> AsyncIterator[httpx.AsyncClient]:
    session_id, _ = await admin_session_manager.initialize_session(2, AdminRole.STAFF)
    try:
        test_client.headers.update({AdminStrings.SESSION_TOKEN_HEADER: session_id})
        yield test_client
    finally:
        await admin_session_manager.terminate_session(session_id, 2)


@pytest.mark.asyncio
async def test_get_key(
    super_admin_test_client: TestClient,
    valid_inactive_keys: list[RotatedKeyDataResult],
) -> None:
    response: Final[httpx.Response] = await super_admin_test_client.get(
        f"cmd/keys/{valid_inactive_keys[0].kid}"
    )
    response_json: dict[str, Any] = response.json()
    response_json.pop("_links", None)
    assert response_json == json_repr(valid_inactive_keys[0])


@pytest.mark.asyncio
async def test_get_missing_key(
    super_admin_test_client: TestClient,
) -> None:
    response: Final[httpx.Response] = await super_admin_test_client.get(
        "cmd/keys/__NON_EXISTENT__"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_normal_key_invalidation(
    super_admin_test_client: TestClient,
    valid_inactive_keys: list[RotatedKeyDataResult],
    synced_key_store_manager: SyncedStoreKeyStateManager,
) -> None:
    target_key: RotatedKeyDataResult = valid_inactive_keys[0]
    response: Final[httpx.Response] = await super_admin_test_client.delete(
        f"/cmd/keys/{target_key.kid}"
    )
    response_json: Final[dict[str, Any]] = response.json()
    assert response_json["purged_key"] == target_key.kid
    assert response_json["valid_keys"] == [
        i.kid for i in await synced_key_store_manager.get_jwks()
    ]


@pytest.mark.asyncio
async def test_get_expired_key_when_requested(
    super_admin_test_client: TestClient,
    valid_inactive_keys: list[RotatedKeyDataResult],
    keydata_repository: KeydataRepository,
) -> None:
    target_key: Final[RotatedKeyDataResult] = valid_inactive_keys[0]
    async with keydata_repository.unit_of_work():
        expired_key = await keydata_repository.expire_keydata(
            target_key.kid, return_expired=True
        )
    assert expired_key is not None

    response: Final[httpx.Response] = await super_admin_test_client.get(
        f"/cmd/keys/{target_key.kid}", params={"include_expired": "true"}
    )
    response_json: Final[dict[str, Any]] = response.json()
    links: Final[list[dict[str, str]]] = response_json.pop("_links")

    assert response.status_code == 200
    assert response_json == json_repr(expired_key)
    assert "private_pem" not in response_json
    assert links == [
        {
            "href": f"/cmd/keys/{target_key.kid}?include_expired=True",
            "rel": "self",
            "type": "GET",
        },
        {"href": "/auth/jwks", "rel": "jwks", "type": "GET"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("GET", "/cmd/keys/5"),
        ("DELETE", "/cmd/keys/4"),
        ("DELETE", "/cmd/keys/"),
        ("POST", "/cmd/keys/rotate"),
    ),
)
async def test_key_routes_require_an_admin_session(
    test_client: httpx.AsyncClient,
    method: str,
    path: str,
) -> None:
    response: Final[httpx.Response] = await test_client.request(method, path)

    assert response.status_code == 401
    assert response.json()["detail"] == (
        f"Missing session token: {AdminStrings.SESSION_TOKEN_HEADER}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ("/cmd/keys/4", "/cmd/keys/"),
)
async def test_staff_cannot_invalidate_keys(
    staff_admin_test_client: httpx.AsyncClient,
    path: str,
) -> None:
    response: Final[httpx.Response] = await staff_admin_test_client.delete(path)

    assert response.status_code == 403
    assert response.json() == {"detail": "Missing permissions for: invalidate_key"}


@pytest.mark.asyncio
async def test_invalidation_expires_key_and_updates_synced_state(
    super_admin_test_client: TestClient,
    valid_inactive_keys: list[RotatedKeyDataResult],
    keydata_repository: KeydataRepository,
    synced_key_store_manager: SyncedStoreKeyStateManager,
) -> None:
    target_key: Final[RotatedKeyDataResult] = valid_inactive_keys[0]

    response: Final[httpx.Response] = await super_admin_test_client.delete(
        f"/cmd/keys/{target_key.kid}"
    )
    persisted_key = await keydata_repository.get_keydata(
        target_key.kid, include_expired=True
    )
    synced_key_ids: Final[list[str]] = [
        key.kid for key in await synced_key_store_manager.get_jwks()
    ]

    assert response.status_code == 200
    assert response.json() == {
        "message": "Key invalidated successfully",
        "purged_key": target_key.kid,
        "valid_keys": synced_key_ids,
        "additional_info": {},
    }
    assert isinstance(persisted_key, ExpiredKeyDataResult)
    assert persisted_key.expired_at is not None
    assert target_key.kid not in synced_key_ids


@pytest.mark.asyncio
async def test_invalidation_repairs_inconsistent_synced_key_state(
    super_admin_test_client: TestClient,
    valid_inactive_keys: list[RotatedKeyDataResult],
    synced_key_store_manager: SyncedStoreKeyStateManager,
) -> None:
    target_key: Final[RotatedKeyDataResult] = valid_inactive_keys[0]
    synced_keys = await synced_key_store_manager.get_jwks()
    await synced_key_store_manager.set_jwks(
        tuple(key for key in synced_keys if key.kid != target_key.kid)
    )

    response: Final[httpx.Response] = await super_admin_test_client.delete(
        f"/cmd/keys/{target_key.kid}"
    )

    assert response.status_code == 200
    assert response.json()["additional_info"] == {
        "keylist_integrity_warning": (
            "Synced keylist state was inconsistent and hence regenerated through "
            "database"
        )
    }
    assert response.json()["valid_keys"] == [
        key.kid for key in await synced_key_store_manager.get_jwks()
    ]


@pytest.mark.asyncio
async def test_active_key_cannot_be_invalidated(
    super_admin_test_client: TestClient,
    keydata_repository: KeydataRepository,
    synced_key_store_manager: SyncedStoreKeyStateManager,
) -> None:
    active_key: Final[
        ActiveKeyPublicDataResult
    ] = await keydata_repository.get_active_key()
    initial_synced_key_ids: Final[list[str]] = [
        key.kid for key in await synced_key_store_manager.get_jwks()
    ]

    response = await super_admin_test_client.delete(f"/cmd/keys/{active_key.kid}")

    assert response.status_code == 409
    assert response.json() == {
        "detail": f"No inactive key with ID {active_key.kid} found"
    }

    assert [
        key.kid for key in await synced_key_store_manager.get_jwks()
    ] == initial_synced_key_ids
    persisted_active_key: Final[
        ActiveKeyPublicDataResult
    ] = await keydata_repository.get_active_key()
    assert persisted_active_key.kid == active_key.kid


@pytest.mark.asyncio
async def test_clean_keystore_preserves_only_active_key(
    super_admin_test_client: TestClient,
    keydata_repository: KeydataRepository,
    synced_key_store_manager: SyncedStoreKeyStateManager,
) -> None:
    active_key: Final[
        ActiveKeyPublicDataResult
    ] = await keydata_repository.get_active_key()
    inactive_keys: Final[
        list[RotatedKeyDataResult]
    ] = await keydata_repository.get_valid_inactive_keys()

    response: Final[httpx.Response] = await super_admin_test_client.delete("/cmd/keys/")

    assert response.status_code == 200
    assert response.json() == {
        "message": "All inactive keys have been invalidated",
        "invalidated keys": [key.kid for key in inactive_keys],
        "active_key": active_key.kid,
    }
    (
        persisted_active_key,
        persisted_rotated_keys,
    ) = await keydata_repository.get_relevant_keydata()
    assert persisted_active_key.kid == active_key.kid
    assert persisted_rotated_keys == []
    assert [key.kid for key in await synced_key_store_manager.get_jwks()] == [
        active_key.kid
    ]


@pytest.mark.asyncio
async def test_rotate_key_updates_database_synced_state_and_response(
    super_admin_test_client: TestClient,
    keydata_repository: KeydataRepository,
    synced_key_store_manager: SyncedStoreKeyStateManager,
    test_superuser_admin_id: int,
) -> None:
    previous_active_key: Final[
        ActiveKeyPublicDataResult
    ] = await keydata_repository.get_active_key()

    response: Final[httpx.Response] = await super_admin_test_client.post(
        "/cmd/keys/rotate"
    )
    response_json: Final[dict[str, Any]] = response.json()
    active_key: Final[
        ActiveKeyPublicDataResult
    ] = await keydata_repository.get_active_key()
    rotated_key: Final[
        RotatedKeyDataResult | None
    ] = await keydata_repository.get_rotated_keydata(previous_active_key.kid)
    assert rotated_key is not None

    assert response.status_code == 201
    assert response_json["message"] == "Key rotation successful"
    assert response_json["previous_kid"] == previous_active_key.kid
    assert response_json["active_key_data"] == json_repr(active_key)
    assert active_key.kid != previous_active_key.kid
    assert rotated_key.rotated_out_at is not None
    assert rotated_key.manual_rotation is True
    assert rotated_key.rotated_by == test_superuser_admin_id
    assert [key.kid for key in await synced_key_store_manager.get_jwks()] == [
        key.kid for key in await keydata_repository.get_jwks()
    ]
    assert response_json["_links"] == [
        {
            "href": f"/cmd/keys/{active_key.kid}",
            "rel": "active-key",
            "type": "GET",
        },
        {
            "href": f"/cmd/keys/{previous_active_key.kid}",
            "rel": "previous-key",
            "type": "GET",
        },
        {"href": "/auth/jwks", "rel": "jwks", "type": "GET"},
    ]
