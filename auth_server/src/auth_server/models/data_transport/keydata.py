from collections.abc import Mapping
from datetime import datetime
from typing import Any, ClassVar, Literal, Self

from auxillary.data_structures.dto import AbstractResult
from auxillary.security.data_structures.jwks.enums import ECAlg, JWKKty, JWKUse
from auxillary.utils import cache_repr, to_base64url
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.types import PublicKeyTypes
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from auth_server.models.database import KeyData


class EllipticCurveJWKResult(AbstractResult):
    resource_name: ClassVar[str] = "EllipticCurveJWK"
    alg: ECAlg
    crv: str
    kid: str
    x: str
    y: str
    kty: Literal[JWKKty.EC] = JWKKty.EC
    use: Literal[JWKUse.SIG] = JWKUse.SIG

    @classmethod
    def initialize_from_cache(cls, cache_mapping: Mapping[str, Any]) -> Self:
        return cls(**cache_mapping)


class BasicKeyPublicDataResult(AbstractResult):
    """
    Basic DTO for KeyData ORM object, excluding private PEM bytes
    """

    resource_name: ClassVar[str] = KeyData.__tablename__

    kid: str
    alg: str
    curve: str
    epoch: datetime
    public_pem: bytes

    def as_jwk(self) -> EllipticCurveJWKResult:
        verification_key: PublicKeyTypes = load_pem_public_key(self.public_pem)
        if not isinstance(verification_key, ec.EllipticCurvePublicKey):
            raise TypeError("Expected an elliptic-curve public key")
        public_numbers: ec.EllipticCurvePublicNumbers = (
            verification_key.public_numbers()
        )
        return EllipticCurveJWKResult(
            alg=ECAlg(self.alg),
            crv=verification_key.curve.name,
            kid=self.kid,
            x=to_base64url(public_numbers.x),
            y=to_base64url(public_numbers.y),
        )


class RotatedKeyDataResult(BasicKeyPublicDataResult):
    """
    DTO for KeyData ORM object, excluding private PEM bytes
    """

    resource_name: ClassVar[str] = KeyData.__tablename__

    rotated_out_at: datetime
    expired_at: None
    manual_rotation: bool | None
    rotated_by: int | None


class ExpiredKeyDataResult(BasicKeyPublicDataResult):
    """
    DTO for KeyData ORM object, excluding private PEM bytes
    """

    resource_name: ClassVar[str] = KeyData.__tablename__

    rotated_out_at: datetime
    expired_at: datetime
    manual_rotation: bool | None
    rotated_by: int | None


class ActiveKeyPublicDataResult(BasicKeyPublicDataResult):
    """
    DTO for KeyData ORM object, excluding private PEM bytes
    """

    resource_name: ClassVar[str] = KeyData.__tablename__

    rotated_out_at: None
    expired_at: None
    manual_rotation: None
    rotated_by: None


class ActiveKeyPrivateDataResult(ActiveKeyPublicDataResult):
    """
    DTO for KeyData ORM object, including private PEM bytes
    """

    private_pem: bytes

    def create_public_copy(self) -> ActiveKeyPublicDataResult:
        return ActiveKeyPublicDataResult.construct_from_cache(cache_repr(self))  # pyrefly: ignore
