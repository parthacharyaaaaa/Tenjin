"""Blueprint routes/views for auth server"""

from enum import StrEnum
from types import MappingProxyType
from typing import Final

from fastapi import APIRouter

from auth_server.routers.admin import ADMIN
from auth_server.routers.auth import AUTH
from auth_server.routers.keys import KEY

__all__ = ("RouterName", "URLPrefix", "ROUTERS", "ROUTER_URL_MAPPING")


class RouterName(StrEnum):
    AUTH = "auth"
    KEY = "key"
    ADMIN = "admin"


class URLPrefix(StrEnum):
    CMD = "cmd"
    ADMIN = "admin"
    AUTH = "auth"
    KEYS = "keys"


ROUTERS: Final[MappingProxyType[RouterName, APIRouter]] = MappingProxyType(
    {
        RouterName.AUTH: AUTH,
        RouterName.KEY: KEY,
        RouterName.ADMIN: ADMIN,
    }
)

ROUTER_URL_MAPPING: Final[MappingProxyType[RouterName, tuple[URLPrefix, ...]]] = (
    MappingProxyType(
        {
            RouterName.AUTH: (URLPrefix.AUTH,),
            RouterName.KEY: (URLPrefix.CMD, URLPrefix.KEYS),
            RouterName.ADMIN: (URLPrefix.CMD, URLPrefix.ADMIN),
        }
    )
)
