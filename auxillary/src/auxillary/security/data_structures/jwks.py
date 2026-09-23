from typing import Annotated, Any, Literal, Protocol

from cryptography.hazmat.primitives.asymmetric.ec import (
    SECP256K1,
    EllipticCurve,
    EllipticCurvePublicNumbers,
)
from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler
from pydantic.functional_serializers import PlainSerializer, model_serializer
from pydantic.functional_validators import BeforeValidator

from auxillary.security.data_structures.jwks_enums import ECAlg, JWKKty, JWKUse
from auxillary.utils import to_base64url

# Common validators
_string_whitespace_remover = BeforeValidator(lambda x: x.strip())


class SupportsJWK(Protocol):
    @property
    def kty(self) -> JWKKty: ...
    @property
    def use(self) -> Literal[JWKUse.SIG]: ...
    @property
    def alg(self) -> str: ...

    kid: str


class SupportsEllipticCurveJWK(SupportsJWK):
    crv: type[EllipticCurve]
    public_memebrs: EllipticCurvePublicNumbers


class JWKS(Protocol):
    keys: list[SupportsJWK]


class GenericJWKMixin(BaseModel):
    kid: Annotated[str, _string_whitespace_remover]
    use: Annotated[Literal[JWKUse.SIG], Field(init=False, frozen=True)] = JWKUse.SIG


class EllipticCurveJWK(GenericJWKMixin, BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    public_memebrs: Annotated[EllipticCurvePublicNumbers, Field(exclude=True)]
    kty: Annotated[Literal[JWKKty.EC], Field(init=False, frozen=True)] = JWKKty.EC
    alg: Annotated[Literal[ECAlg.ES256], Field(init=False, frozen=True)] = ECAlg.ES256
    crv: Annotated[
        type[EllipticCurve], Field(frozen=True), PlainSerializer(EllipticCurve.name)
    ] = SECP256K1

    @model_serializer(mode="wrap")
    def serialize_model(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        model_dump: dict[str, Any] = handler(self)
        model_dump["x"] = to_base64url(self.public_memebrs.x)
        model_dump["y"] = to_base64url(self.public_memebrs.y)
        return model_dump


class EllipticCurveJWKS(BaseModel):
    keys: list[EllipticCurveJWK]
