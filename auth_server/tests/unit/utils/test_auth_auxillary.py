from datetime import UTC, datetime
from typing import Any

from auth_server.utils.auth_auxillary import attach_tokens
from fastapi.datastructures import URL
from fastapi.responses import JSONResponse


def _normalize_raw_cookies(cookies: list[bytes]) -> dict[str, Any]:
    d: dict[str, Any] = {}
    for i in cookies:
        processed_i = i.split(b"=")
        assert len(processed_i) <= 2, "Invalid cookie syntax"
        if len(processed_i) == 1:
            d[processed_i[0].decode("utf-8")] = True
        else:
            d[processed_i[0].decode("utf-8")] = processed_i[1].decode("utf-8")
    return d


def common_cookie_assertions(cookie_dict: dict[str, Any]) -> None:
    assert "HttpOnly" in cookie_dict
    assert cookie_dict["SameSite"] == "lax"


def test_attach_tokens_on_request(dummy_json_response: JSONResponse) -> None:
    access_token, refresh_token = "access", "refresh"
    refresh_max_age = access_max_age = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())
    cookie_paths: tuple[URL, ...] = tuple(map(URL, ("/foo", "/bar")))
    attach_tokens(
        dummy_json_response,
        access_token,
        refresh_token,
        access_max_age,
        refresh_max_age,
        cookie_paths,
    )

    access_cookie_data: list[bytes] | None = None
    refresh_cookie_data: list[list[bytes]] = []

    for header in dummy_json_response.headers.raw:
        if header[0] == b"set-cookie":
            if header[1].startswith(b"access=access"):
                access_cookie_data = header[1].split(b"; ")
                continue
            if header[1].startswith(b"refresh=refresh"):
                refresh_cookie_data.append(header[1].split(b"; "))
    assert access_cookie_data, "Missing access token"
    assert len(refresh_cookie_data) == 2, "Invalid refresh token count"

    print(access_cookie_data)
    normalized_access_cookies = _normalize_raw_cookies(access_cookie_data)
    common_cookie_assertions(normalized_access_cookies)
    assert int(normalized_access_cookies["Max-Age"]) == access_max_age
    assert normalized_access_cookies["Path"] == "/"

    normalized_refresh_cookie_data = tuple(
        _normalize_raw_cookies(i) for i in refresh_cookie_data
    )
    assert (
        normalized_refresh_cookie_data[0]["Max-Age"]
        == normalized_refresh_cookie_data[1]["Max-Age"]
    )
    assert int(normalized_refresh_cookie_data[0]["Max-Age"]) == refresh_max_age
    assert (
        normalized_refresh_cookie_data[0]["Path"] == "/bar"
        and normalized_refresh_cookie_data[1]["Path"] == "/foo"
    ) or (
        normalized_refresh_cookie_data[0]["Path"] == "/foo"
        and normalized_refresh_cookie_data[1]["Path"] == "/bar"
    )
