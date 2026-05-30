"""Blueprint routes/views for auth server"""

from enum import StrEnum
from types import MappingProxyType
from typing import Final

from fastapi import APIRouter

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
    ADMIN = "admin"
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
