from types import MappingProxyType
from typing import Final, TypedDict
from enum import Enum

__all__ = ("TokenType",
           "StandardAccessTokenClaims",
           "StandardRefreshTokenClaims",
           "PAYLOAD_MAPPING")

class TokenType(str, Enum):
    StandardAccess = "StandardAccess"
    StandardRefresh = "StandardRefresh"

class StandardAccessTokenClaims(TypedDict):
    iat: float
    exp: float
    fid: str
    sub: str
    sid: int
    jti: str|None

class StandardRefreshTokenClaims(StandardAccessTokenClaims):
    nbf: float

PAYLOAD_MAPPING: Final[MappingProxyType] = MappingProxyType(
    {
        TokenType.StandardAccess: StandardAccessTokenClaims,
        TokenType.StandardRefresh: StandardRefreshTokenClaims
    }
)
