from enum import StrEnum


class JWKUse(StrEnum):
    SIG = "sig"
    ENC = "enc"


class JWKKty(StrEnum):
    RSA = "RSA"
    EC = "EC"


class ECAlg(StrEnum):
    ES256 = "ES256"
