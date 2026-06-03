from typing import Literal, TypedDict


class JWKSEntry(TypedDict):
    kty: str
    kid: str
    use: Literal["sig"]
    alg: str
    crv: str
    x: str
    y: str


class StandardAccessTokenClaims(TypedDict):
    iat: float
    exp: float
    fid: str
    sub: str
    sid: int
    jti: str | None
