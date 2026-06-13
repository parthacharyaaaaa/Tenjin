"""Helper functions"""

import datetime
import hashlib
import os
import traceback
from typing import Final, Literal, Mapping, Callable, Any
from types import NoneType
import base64

import bcrypt

from fastapi import Request, Response, HTTPException
from fastapi.responses import JSONResponse

from redis.typing import FieldT, EncodableT

from auxillary.typing_utils import SupportsJSON, SupportsCache


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


def hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    """
    Produce a password salt and hash from a given string

    returns: tuple[password-hash, salt]"""
    if salt is None:
        salt = os.urandom(16)
    passwordHash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100000)
    return passwordHash, salt


def bcrypt_hash_password(
    password: str,
    *,
    salt: bytes | None = None,
    salt_generation_rounds: int = 12,
    salt_prefix: Literal[b"2", b"2a", b"2x", b"2y", b"2b"] = b"2b",
    password_codec: str = "utf-8",
) -> bytes:
    if not salt:
        salt = bcrypt.gensalt(salt_generation_rounds, salt_prefix)
    return bcrypt.hashpw(password.encode(password_codec), salt)


def bcrypt_check_password(
    password: str, password_hash: bytes, *, password_codec: str = "utf-8"
) -> bool:
    return bcrypt.checkpw(password.encode(password_codec), password_hash)


def verify_password(password: str, password_hash: bytes, salt: bytes) -> bool:
    """
    Match a given password and salt with a hashed password
    """
    return (
        hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100000) == password_hash
    )


def rediserialize(
    mapping: dict,
    typeMapping: Mapping[type, Callable] = {
        NoneType: lambda _: "",
        bool: lambda b: int(b),
        datetime.datetime: lambda dt: dt.isoformat(),
        list: lambda l: ":".join(l),
    },
) -> dict:
    """Serialize a Python dictionary to a Redis hashmap"""
    return {k: typeMapping.get(type(v), lambda x: x)(v) for k, v in mapping.items()}


def pyserialize(
    mapping: dict[str, str],
    deserialize_mapping: dict[str, type[Any]],
    strict: bool = False,
) -> dict[str, Any]:
    """Deserialize a Redis hashmap back to its original Python model's __json_like__() dictionary
    Args:
        mapping: Redis hashmap to deserialize
        deserialize_mapping: Mapping of key values and their intended types. These types can also be lambda functions to allow for casts more complex than constructor calls
        strict: If True, mapping and deserialize mapping must have the same keys

    Raises:
        ValueError: If strict is True and mappings don't match
        ValueError: Intended function cannot cast the string to the intended Python type
    Returns:
        Deserialized Python dictionary
    """
    if strict and set(mapping.keys()) != set(deserialize_mapping.keys()):
        raise ValueError("Mappings do not match")
    return {
        key: deserialize_mapping[key](value) if key in deserialize_mapping else value
        for key, value in mapping.items()
    }


def genericDBFetchException():
    """Generic fetch exception handler"""
    exc = Exception()
    exc.__setattr__("description", "An error occurred when fetching this resource")
    raise exc


def json_repr(arg: SupportsJSON) -> dict[str, Any]:
    return arg.__json_repr__()


def cache_repr(arg: SupportsCache) -> dict[FieldT, EncodableT]:
    return arg.__cache_repr__()
