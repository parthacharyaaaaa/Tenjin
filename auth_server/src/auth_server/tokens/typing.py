from enum import StrEnum
from types import MappingProxyType
from typing import Final, TypedDict

__all__ = (
    "TokenType",
    "StandardAccessTokenClaims",
    "StandardRefreshTokenClaims",
    "PAYLOAD_MAPPING",
)


class TokenType(StrEnum):
    StandardAccess = "StandardAccess"
    StandardRefresh = "StandardRefresh"


class StandardAccessTokenClaims(TypedDict):
    iat: float
    exp: int
    fid: str
    sub: str
    sid: int
    jti: str


class StandardRefreshTokenClaims(StandardAccessTokenClaims):
    nbf: float


PAYLOAD_MAPPING: Final[MappingProxyType] = MappingProxyType(
    {
        TokenType.StandardAccess: StandardAccessTokenClaims,
        TokenType.StandardRefresh: StandardRefreshTokenClaims,
    }
)
