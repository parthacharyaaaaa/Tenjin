from collections.abc import AsyncIterator, Generator
from datetime import timedelta
from http.cookies import SimpleCookie
from typing import Any, Final
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
from auth_server.repositories.keydata import (
    KeydataRepository,
    KeyPrivateDataResult,
)
from auth_server.subsystems.key_manager import SyncedStoreKeyStateManager
from auth_server.subsystems.token_manager import TokenManager
from auth_server.tokens.typing import TokenType
from auxillary.utils import json_repr
from redis.asyncio.client import Redis

AUTH_ROOT: Final[str] = "/auth"
VALID_PASSWORD: Final[str] = "correct-password"


class ResourceClient:
    def __init__(self) -> None:
        self.post = AsyncMock()

    async def __aenter__(self) -> "ResourceClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


@pytest_asyncio.fixture(autouse=True)
async def prepare_auth_test_state(
    prepare_database_state: None,
    keydata_repository: KeydataRepository,
    synced_key_store_manager: SyncedStoreKeyStateManager,
    token_manager: TokenManager,
    token_store_client: Redis,
    async_redis_client: Redis,
) -> AsyncIterator[None]:
    await token_store_client.flushall()
    await async_redis_client.flushall()

    keydata: Final[
        list[KeyPrivateDataResult]
    ] = await keydata_repository.get_relevant_keydata(public_data_only=False)
    active_key: Final[KeyPrivateDataResult] = next(
        key for key in keydata if key.rotated_out_at is None
    )
    token_manager.set_key_state(
        active_key,
        {
            key.kid: key.create_public_copy()
            for key in keydata
            if key.kid != active_key.kid
        },
    )
    await synced_key_store_manager.set_jwks(await keydata_repository.get_jwks())

    try:
        yield
    finally:
        await token_store_client.flushall()
        await async_redis_client.flushall()


@pytest.fixture
def resource_post() -> Generator[AsyncMock, None, None]:
    resource_client = ResourceClient()
    with patch(
        "auth_server.routers.auth.httpx.AsyncClient",
        return_value=resource_client,
    ):
        yield resource_client.post


def _response_cookies(response: httpx.Response, name: str) -> list[dict[str, str]]:
    cookies: list[dict[str, str]] = []
    for header in response.headers.get_list("set-cookie"):
        parsed = SimpleCookie()
        parsed.load(header)
        if morsel := parsed.get(name):
            cookies.append(
                {
                    "value": morsel.value,
                    "path": morsel["path"],
                    "max-age": morsel["max-age"],
                    "httponly": morsel["httponly"],
                }
            )
    return cookies


def _set_resource_response(
    resource_post: AsyncMock,
    status_code: int,
    payload: dict[str, Any],
) -> None:
    resource_post.return_value = httpx.Response(status_code, json=payload)


@pytest.mark.asyncio
async def test_jwks_returns_synced_public_keys(
    test_client: httpx.AsyncClient,
    synced_key_store_manager: SyncedStoreKeyStateManager,
) -> None:
    expected_keys = await synced_key_store_manager.get_jwks()

    response = await test_client.get(f"{AUTH_ROOT}/jwks")

    assert response.status_code == 200
    assert response.json() == {"keys": [json_repr(key) for key in expected_keys]}


@pytest.mark.asyncio
async def test_login_forwards_normalized_credentials_and_issues_tokens(
    test_client: httpx.AsyncClient,
    resource_post: AsyncMock,
    token_manager: TokenManager,
    token_store_client: Redis,
) -> None:
    _set_resource_response(
        resource_post,
        200,
        {
            "sub": "user_123",
            "sid": 42,
            "message": "Welcome back",
            "display_name": "Test User",
        },
    )

    response = await test_client.post(
        f"{AUTH_ROOT}/login",
        json={"identity": "  user_123  ", "password": f"  {VALID_PASSWORD}  "},
    )

    resource_post.assert_awaited_once_with(
        url="",
        json={"identity": "user_123", "password": VALID_PASSWORD},
    )
    assert response.status_code == 201
    assert response.json() == {
        "message": "Welcome back",
        "username": "user_123",
        "_additional": {"display_name": "Test User"},
        "_links": [
            {"href": "/auth/reissue", "rel": "refresh", "type": "GET"},
            {"href": "/auth/tokens", "rel": "logout", "type": "DELETE"},
        ],
    }

    access_cookies = _response_cookies(response, "access")
    refresh_cookies = _response_cookies(response, "refresh")
    assert len(access_cookies) == 1
    assert len(refresh_cookies) == 2
    assert access_cookies[0]["path"] == "/"
    assert {cookie["path"] for cookie in refresh_cookies} == {
        "/auth/reissue",
        "/auth/tokens",
    }
    assert all(cookie["httponly"] for cookie in access_cookies + refresh_cookies)
    assert int(access_cookies[0]["max-age"]) == int(
        (
            token_manager.token_manager_config.ACCESS_LIFETIME
            + token_manager.token_manager_config.LEEWAY
        ).total_seconds()
    )
    assert all(
        int(cookie["max-age"])
        == int(
            (
                token_manager.token_manager_config.REFRESH_LIFETIME
                + token_manager.token_manager_config.LEEWAY
            ).total_seconds()
        )
        for cookie in refresh_cookies
    )

    access_claims = await token_manager.decode_token(access_cookies[0]["value"])
    refresh_claims = await token_manager.decode_token(
        refresh_cookies[0]["value"],
        TokenType.StandardRefresh,
        options={"verify_nbf": False},
    )
    assert (access_claims["sub"], access_claims["sid"]) == ("user_123", 42)
    assert (refresh_claims["sub"], refresh_claims["sid"]) == ("user_123", 42)
    assert access_claims["fid"] == refresh_claims["fid"]
    assert await token_store_client.llen(refresh_claims["fid"]) == 1  # pyrefly: ignore[not-async]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "upstream_payload", "expected_message"),
    (
        (401, {"message": "Invalid credentials"}, "Invalid credentials"),
        (403, {"message": "Account disabled"}, "Account disabled"),
        (503, {}, "None"),
    ),
)
async def test_login_propagates_resource_server_rejection(
    test_client: httpx.AsyncClient,
    resource_post: AsyncMock,
    token_store_client: Redis,
    status_code: int,
    upstream_payload: dict[str, str],
    expected_message: str,
) -> None:
    _set_resource_response(resource_post, status_code, upstream_payload)

    response = await test_client.post(
        f"{AUTH_ROOT}/login",
        json={"identity": "user_123", "password": VALID_PASSWORD},
    )

    assert response.status_code == status_code
    assert response.json() == {
        "message": "Authentication Failed",
        "response_message": expected_message,
    }
    assert response.headers.get_list("set-cookie") == []
    assert await token_store_client.keys("FID:*") == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    (
        {"identity": "bad identity", "password": VALID_PASSWORD},
        {"identity": "user_123", "password": "short"},
        {"identity": "user_123"},
        {"identity": "admin' OR '1'='1", "password": VALID_PASSWORD},
    ),
)
async def test_login_rejects_invalid_requests_before_calling_resource_server(
    test_client: httpx.AsyncClient,
    resource_post: AsyncMock,
    payload: dict[str, str],
) -> None:
    response = await test_client.post(f"{AUTH_ROOT}/login", json=payload)

    assert response.status_code == 422
    resource_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_registration_forwards_normalized_data_and_issues_tokens(
    test_client: httpx.AsyncClient,
    resource_post: AsyncMock,
    token_manager: TokenManager,
) -> None:
    _set_resource_response(
        resource_post,
        201,
        {
            "sub": "new_user",
            "sid": 73,
            "email": "new@example.com",
            "message": "Account created",
            "profile_complete": False,
        },
    )

    response = await test_client.post(
        f"{AUTH_ROOT}/register",
        json={
            "identity": "  new_user  ",
            "email": "  new@example.com  ",
            "password": f"  {VALID_PASSWORD}  ",
        },
    )

    resource_post.assert_awaited_once_with(
        "",
        json={
            "identity": "new_user",
            "email": "new@example.com",
            "password": VALID_PASSWORD,
        },
    )
    assert response.status_code == 201
    assert response.json() == {
        "message": "Account created",
        "username": "new_user",
        "email": "new@example.com",
        "issuer": "tenjin-AUTH-service",
        "_additional": {"profile_complete": False},
        "_links": [
            {"href": "/auth/reissue", "rel": "refresh", "type": "GET"},
            {"href": "/auth/tokens", "rel": "logout", "type": "DELETE"},
        ],
    }

    access_cookie = _response_cookies(response, "access")[0]
    refresh_cookie = _response_cookies(response, "refresh")[0]
    access_claims = await token_manager.decode_token(access_cookie["value"])
    refresh_claims = await token_manager.decode_token(
        refresh_cookie["value"],
        TokenType.StandardRefresh,
        options={"verify_nbf": False},
    )
    assert (access_claims["sub"], access_claims["sid"]) == ("new_user", 73)
    assert (refresh_claims["sub"], refresh_claims["sid"]) == ("new_user", 73)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "upstream_payload", "expected_message"),
    (
        (400, {"message": "Email already registered"}, "Email already registered"),
        (409, {"message": "Username unavailable"}, "Username unavailable"),
        (503, {}, None),
    ),
)
async def test_registration_propagates_resource_server_rejection(
    test_client: httpx.AsyncClient,
    resource_post: AsyncMock,
    token_store_client: Redis,
    status_code: int,
    upstream_payload: dict[str, str],
    expected_message: str | None,
) -> None:
    _set_resource_response(resource_post, status_code, upstream_payload)

    response = await test_client.post(
        f"{AUTH_ROOT}/register",
        json={
            "identity": "new_user",
            "email": "new@example.com",
            "password": VALID_PASSWORD,
        },
    )

    assert response.status_code == status_code
    assert response.json() == {
        "message": "Failed to create account",
        "response_message": expected_message,
    }
    assert response.headers.get_list("set-cookie") == []
    assert await token_store_client.keys("FID:*") == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    (
        {
            "identity": "user@example.com",
            "email": "user@example.com",
            "password": VALID_PASSWORD,
        },
        {
            "identity": "new_user",
            "email": "not-an-email",
            "password": VALID_PASSWORD,
        },
        {
            "identity": "new_user",
            "email": "new@example.com",
            "password": "short",
        },
    ),
)
async def test_registration_rejects_invalid_requests_before_upstream_call(
    test_client: httpx.AsyncClient,
    resource_post: AsyncMock,
    payload: dict[str, str],
) -> None:
    response = await test_client.post(f"{AUTH_ROOT}/register", json=payload)

    assert response.status_code == 422
    resource_post.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cookie_name", ("refresh", "Refresh"))
async def test_reissue_rotates_tokens_and_extends_existing_family(
    test_client: httpx.AsyncClient,
    token_manager: TokenManager,
    token_store_client: Redis,
    cookie_name: str,
) -> None:
    with patch.object(
        token_manager.token_manager_config,
        "ACCESS_LIFETIME",
        timedelta(),
    ):
        old_refresh_token = await token_manager.issue_refresh_token("user_123", 42)
        family_id = token_manager.generate_family_list_id("user_123")

        response = await test_client.get(
            f"{AUTH_ROOT}/reissue",
            headers={"cookie": f"{cookie_name}={old_refresh_token}"},
        )

    assert response.status_code == 201
    assert response.json() == {
        "message": "Reissuance successful",
        "_links": [
            {"href": "/auth/reissue", "rel": "refresh", "type": "GET"},
            {"href": "/auth/tokens", "rel": "logout", "type": "DELETE"},
        ],
    }
    assert await token_store_client.llen(family_id) == 2  # pyrefly: ignore[not-async]

    access_cookie = _response_cookies(response, "access")[0]
    refresh_cookie = _response_cookies(response, "refresh")[0]
    assert refresh_cookie["value"] != old_refresh_token
    access_claims = await token_manager.decode_token(access_cookie["value"])
    refresh_claims = await token_manager.decode_token(
        refresh_cookie["value"],
        TokenType.StandardRefresh,
        options={"verify_nbf": False},
    )
    assert access_claims["fid"] == family_id
    assert refresh_claims["fid"] == family_id


@pytest.mark.asyncio
async def test_reissue_requires_refresh_cookie(
    test_client: httpx.AsyncClient,
) -> None:
    response = await test_client.get(f"{AUTH_ROOT}/reissue")

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Refresh token missing from request, reissuance denied"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cookie_name", ("refresh", "Refresh"))
async def test_purge_family_revokes_valid_refresh_token(
    test_client: httpx.AsyncClient,
    token_manager: TokenManager,
    token_store_client: Redis,
    cookie_name: str,
) -> None:
    refresh_token = await token_manager.issue_refresh_token("user_123", 42)
    family_id = token_manager.generate_family_list_id("user_123")

    response = await test_client.delete(
        f"{AUTH_ROOT}/tokens",
        headers={"cookie": f"{cookie_name}={refresh_token}"},
    )

    assert response.status_code == 200
    assert response.json() == {"message": "Token Revoked"}
    assert await token_store_client.llen(family_id) == 0  # pyrefly: ignore[not-async]


@pytest.mark.asyncio
async def test_purge_family_requires_refresh_cookie(
    test_client: httpx.AsyncClient,
) -> None:
    response = await test_client.delete(f"{AUTH_ROOT}/tokens")

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Logout requires a refresh token to be provided"
    )


@pytest.mark.asyncio
async def test_purge_family_rejects_invalid_refresh_token(
    test_client: httpx.AsyncClient,
) -> None:
    response = await test_client.delete(
        f"{AUTH_ROOT}/tokens",
        headers={"cookie": "refresh=not-a-jwt"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Failed to validate this refresh token"
