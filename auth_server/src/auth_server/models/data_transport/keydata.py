from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Literal, Self

from auxillary.data_structures.dto import AbstractResult
from auxillary.security.data_structures.jwks.enums import ECAlg, JWKKty, JWKUse
from auxillary.utils import cache_repr, to_base64url
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.types import PublicKeyTypes
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from redis.typing import EncodableT, FieldT

from auth_server.models.database import KeyData


@dataclass(slots=True)
class EllipticCurveJWKResult(AbstractResult):
    resource_name: ClassVar[str] = "EllipticCurveJWK"
    alg: ECAlg
    crv: str
    kid: str
    x: str
    y: str
    kty: Literal[JWKKty.EC] = field(default=JWKKty.EC)
    use: Literal[JWKUse.SIG] = field(default=JWKUse.SIG)

    @classmethod
    def initialize_from_cache(cls, cache_mapping: Mapping[str, Any]) -> Self:
        return cls(**cache_mapping)


@dataclass(slots=True, init=False)
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

    @staticmethod
    def stringify_binary_fields(
        d: MutableMapping[Any, Any], *, encoding: str = "utf-8"
    ) -> None:
        for k, v in d.items():
            if isinstance(v, (bytes, bytearray)):
                d[k] = v.decode(encoding)

    def __json_repr__(self) -> dict[str, Any]:
        json_mapping: dict[str, Any] = super(
            BasicKeyPublicDataResult, self
        ).__json_repr__()
        self.stringify_binary_fields(json_mapping)
        return json_mapping

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        cache_mapping: dict[FieldT, EncodableT] = super(
            BasicKeyPublicDataResult, self
        ).__cache_repr__()
        self.stringify_binary_fields(cache_mapping)
        return cache_mapping

    @classmethod
    def construct_from_cache(cls, mapping: Mapping[str, Any], *args, **kwargs) -> Self:
        instance: cls = super(BasicKeyPublicDataResult, cls).construct_from_cache(
            mapping
        )
        instance.public_pem = instance.public_pem.encode(encoding="ascii")  # type: ignore
        return instance

    def as_jwk(self) -> EllipticCurveJWKResult:
        verification_key: PublicKeyTypes = load_pem_public_key(self.public_pem)
        if not isinstance(verification_key, ec.EllipticCurvePublicKey):
            raise TypeError("Expected an elliptic-curve public key")
        public_numbers: ec.EllipticCurvePublicNumbers = (
            verification_key.public_numbers()
        )
        return EllipticCurveJWKResult(
            ECAlg(self.alg),
            verification_key.curve.name,
            self.kid,
            to_base64url(public_numbers.x),
            to_base64url(public_numbers.y),
        )


@dataclass(slots=True, init=False)
class RotatedKeyDataResult(BasicKeyPublicDataResult):
    """
    DTO for KeyData ORM object, excluding private PEM bytes
    """

    resource_name: ClassVar[str] = KeyData.__tablename__

    rotated_out_at: datetime
    expired_at: None
    manual_rotation: bool | None
    rotated_by: int | None


@dataclass(slots=True, init=False)
class ExpiredKeyDataResult(BasicKeyPublicDataResult):
    """
    DTO for KeyData ORM object, excluding private PEM bytes
    """

    resource_name: ClassVar[str] = KeyData.__tablename__

    rotated_out_at: datetime
    expired_at: datetime
    manual_rotation: bool | None
    rotated_by: int | None


@dataclass(slots=True, init=False)
class ActiveKeyPublicDataResult(BasicKeyPublicDataResult):
    """
    DTO for KeyData ORM object, excluding private PEM bytes
    """

    resource_name: ClassVar[str] = KeyData.__tablename__

    rotated_out_at: None
    expired_at: None
    manual_rotation: None
    rotated_by: None


@dataclass(slots=True, init=False)
class ActiveKeyPrivateDataResult(ActiveKeyPublicDataResult):
    """
    DTO for KeyData ORM object, including private PEM bytes
    """

    private_pem: bytes

    @classmethod
    def construct_from_cache(cls, mapping: Mapping[str, Any], *args, **kwargs) -> Self:
        instance: cls = super(BasicKeyPublicDataResult, cls).construct_from_cache(
            mapping
        )
        instance.private_pem = instance.private_pem.encode(encoding="ascii")  # type: ignore
        return instance

    def create_public_copy(self) -> ActiveKeyPublicDataResult:
        return ActiveKeyPublicDataResult.construct_from_cache(cache_repr(self))  # pyrefly: ignore
