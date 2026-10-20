"""Blueprint routes/views for auth server"""

from collections.abc import Callable
from enum import StrEnum
from types import MappingProxyType
from typing import Final, ParamSpec

from auxillary.data_structures.enriched.exceptions import EnrichedHTTPException
from auxillary.utils import ExceptionHandler, generic_error_handler
from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import Response

from auth_server.routers.admin import ADMIN
from auth_server.routers.auth import AUTH
from auth_server.routers.keys import KEY

__all__ = ("RouterName", "URLPrefix", "ROUTER_URL_MAPPING")


class RouterName(StrEnum):
    AUTH = "auth"
    KEY = "key"
    ADMIN = "admin"


class URLPrefix(StrEnum):
    CMD = "cmd"
    ADMIN = "admins"
    AUTH = "auth"
    KEYS = "keys"


ROUTER_URL_MAPPING: Final[
    MappingProxyType[RouterName, tuple[APIRouter, tuple[URLPrefix, ...]]]
] = MappingProxyType(
    {
        RouterName.AUTH: (AUTH, (URLPrefix.AUTH,)),
        RouterName.KEY: (KEY, (URLPrefix.CMD, URLPrefix.KEYS)),
        RouterName.ADMIN: (ADMIN, (URLPrefix.CMD, URLPrefix.ADMIN)),
    }
)

EXCEPTION_HANDLER_MAPPING: Final[
    MappingProxyType[type[Exception], ExceptionHandler]
] = MappingProxyType({Exception: generic_error_handler})
