"""Blueprint routes/views for auth server"""

from enum import Enum
from types import MappingProxyType
from typing import Final

from flask import Blueprint

from auth_server.blueprints.blueprint_cmd import cmd
from auth_server.blueprints.blueprint_routes import auth

__all__ = ("URLPrefix", "BLUEPRINT_URL_MAPPING")


class URLPrefix(str, Enum):
    CMD = "cmd"
    AUTH = "auth"


BLUEPRINT_URL_MAPPING: Final[MappingProxyType[Blueprint, URLPrefix]] = MappingProxyType(
    {cmd: URLPrefix.CMD, auth: URLPrefix.AUTH}
)
