"""Helper functions"""

import base64
import traceback
from typing import Any, Final

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from redis.typing import EncodableT, FieldT

from auxillary.typing_utils import SupportsCache, SupportsJSON


def generic_error_handler(r: Request, e: Exception) -> Response:
    print(traceback.format_exc())

    if not isinstance(e, HTTPException):
        e = HTTPException(500, "An error occured")

    response: Final[JSONResponse] = JSONResponse(
        status_code=e.status_code,
        content={"message": e.detail, **getattr(e, "kwargs", {})},
    )
    response.headers.update(e.headers or {})

    return response


def to_base64url(n: int, length: int = 32) -> str:
    return (
        base64.urlsafe_b64encode(n.to_bytes(length, byteorder="big"))
        .rstrip(b"=")
        .decode("utf-8")
    )


def from_base64url(b64url: str) -> int:
    # Add back padding if needed
    padding = "=" * ((4 - len(b64url) % 4) % 4)
    padded_b64url = b64url + padding
    byte_data = base64.urlsafe_b64decode(padded_b64url)
    return int.from_bytes(byte_data, byteorder="big")


def generic_database_fetch_exception():
    """Generic fetch exception handler"""
    exc = Exception()
    exc.__setattr__("description", "An error occurred when fetching this resource")
    raise exc


def json_repr(arg: SupportsJSON) -> dict[str, Any]:
    return arg.__json_repr__()


def cache_repr(arg: SupportsCache) -> dict[FieldT, EncodableT]:
    return arg.__cache_repr__()
