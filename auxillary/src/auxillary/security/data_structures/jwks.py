from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Annotated, Any, ClassVar, Final, Literal, Self

from cryptography.hazmat.primitives.asymmetric.ec import (
    SECP256K1,
    EllipticCurve,
    EllipticCurvePublicNumbers,
)
from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler
from pydantic.functional_serializers import PlainSerializer, model_serializer
from pydantic.functional_validators import BeforeValidator

from auxillary.security.data_structures.jwks_enums import ECAlg, JWKKty, JWKUse
from auxillary.security.data_structures.jwks_typing import (
    SupportsJWK,
    SupportsJWKSerialization,
)
from auxillary.utils import json_repr, to_base64url

# Common validators
_string_whitespace_remover = BeforeValidator(lambda x: x.strip())


class GenericJWKMixin:
    kid: Annotated[str, _string_whitespace_remover, Field(frozen=True)]
    use: Annotated[Literal[JWKUse.SIG], Field(init=False, frozen=True)] = JWKUse.SIG


class EllipticCurveJWK(GenericJWKMixin, BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    public_members: Annotated[EllipticCurvePublicNumbers, Field(exclude=True)]
    kty: Annotated[Literal[JWKKty.EC], Field(init=False, frozen=True)] = JWKKty.EC
    alg: Annotated[Literal[ECAlg.ES256], Field(init=False, frozen=True)] = ECAlg.ES256
    crv: Annotated[
        type[EllipticCurve], Field(frozen=True), PlainSerializer(EllipticCurve.name)
    ] = SECP256K1

    @model_serializer(mode="wrap")
    def serialize_model(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        model_dump: dict[str, Any] = handler(self)
        model_dump.pop("public_members")
        model_dump["x"] = to_base64url(self.public_members.x)
        model_dump["y"] = to_base64url(self.public_members.y)
        return model_dump

    def __json_repr__(self) -> dict[str, Any]:
        return self.model_dump()


class EllipticCurveJWKS(BaseModel):
    keys: list[EllipticCurveJWK]

    @classmethod
    def construct_from_jwks_list(cls, jwks_list: Sequence[Mapping[str, Any]]) -> Self:
        parsed_keydata: Final[list[SupportsJWK]] = []
        for jwks_entry in jwks_list:
            parsed_keydata.append(EllipticCurveJWK.model_validate(jwks_entry))
        return cls(keys=parsed_keydata)

    def __json_repr__(self) -> dict[str, Any]:
        return {"keys": [json_repr(k) for k in self.keys]}


class VariableJWKS(BaseModel):
    _casting_map: ClassVar[MappingProxyType[JWKKty, type[SupportsJWKSerialization]]] = (
        MappingProxyType[JWKKty, type[SupportsJWKSerialization]](
            {
                JWKKty.EC: EllipticCurveJWK  # pyrefly: ignore[bad-assignment]
            }
        )
    )
    keys: list[EllipticCurveJWK]  # To be unionized if we add other key types

    @classmethod
    def construct_from_jwks_list(cls, jwks_list: Sequence[Mapping[str, Any]]) -> Self:
        parsed_keydata: Final[list[SupportsJWK]] = []
        for jwks_entry in jwks_list:
            key_type: JWKKty = JWKKty(jwks_entry["kty"])
            parsed_keydata.append(cls._casting_map[key_type].model_validate(jwks_entry))
        return cls(keys=parsed_keydata)

    def __json_repr__(self) -> dict[str, Any]:
        return {"keys": [json_repr(k) for k in self.keys]}
