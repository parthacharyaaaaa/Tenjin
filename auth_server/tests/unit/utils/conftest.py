from typing import Any

import pytest
from fastapi.requests import Request
from fastapi.responses import JSONResponse


@pytest.fixture
def bare_get_request_http_scope() -> dict[str, Any]:
    return {
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": "/foo",
        "raw_path": b"/foo",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
        ],
    }


@pytest.fixture
def dummy_get_fastapi_request(bare_get_request_http_scope) -> Request:
    return Request(scope=bare_get_request_http_scope)


@pytest.fixture
def dummy_json_response() -> JSONResponse:
    return JSONResponse({"message": "foo"})
