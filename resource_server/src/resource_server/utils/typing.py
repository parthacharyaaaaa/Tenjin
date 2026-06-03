from typing import Literal, TypedDict


class JWKSEntry(TypedDict):
    kty: str
    kid: str
    use: Literal["sig"]
    alg: str
    crv: str
    x: str
    y: str
