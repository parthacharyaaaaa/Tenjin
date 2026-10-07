from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Annotated, Any, ClassVar, Final, Literal, Self, TypeVar

from cryptography.hazmat.primitives.asymmetric.ec import (
    SECP192R1,
    SECP224R1,
    SECP256K1,
    SECP256R1,
    SECP384R1,
    SECP521R1,
    BrainpoolP256R1,
    BrainpoolP384R1,
    BrainpoolP512R1,
    EllipticCurve,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)
from pydantic.functional_serializers import PlainSerializer
from pydantic.functional_validators import BeforeValidator

from auxillary.security.data_structures.jwks.enums import ECAlg, JWKKty, JWKUse
from auxillary.security.data_structures.jwks.typing import (
    SupportsJWK,
    SupportsJWKSerialization,
)
from auxillary.utils import from_base64url, json_repr, to_base64url

# Common validators
_string_whitespace_remover = BeforeValidator(lambda x: x.strip())

AnyT = TypeVar("AnyT")


class GenericJWKMixin:
    kid: Annotated[str, _string_whitespace_remover, Field(frozen=True)]
    use: Annotated[Literal[JWKUse.SIG], Field(init=False, frozen=True)] = JWKUse.SIG


class EllipticCurveJWK(GenericJWKMixin, BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    CURVE_TYPES: ClassVar[dict[str, EllipticCurve]] = {
        "prime192v1": SECP192R1(),
        "prime256v1": SECP256R1(),
        "secp192r1": SECP192R1(),
        "secp224r1": SECP224R1(),
        "secp256r1": SECP256R1(),
        "secp384r1": SECP384R1(),
        "secp521r1": SECP521R1(),
        "secp256k1": SECP256K1(),
        "brainpoolP256r1": BrainpoolP256R1(),
        "brainpoolP384r1": BrainpoolP384R1(),
        "brainpoolP512r1": BrainpoolP512R1(),
    }

    x: Annotated[str, _string_whitespace_remover]
    y: Annotated[str, _string_whitespace_remover]
    kty: Annotated[Literal[JWKKty.EC], Field(init=False, frozen=True)] = JWKKty.EC
    alg: Annotated[Literal[ECAlg.ES256], Field(init=False, frozen=True)] = ECAlg.ES256
    crv: Annotated[
        EllipticCurve,
        Field(frozen=True, default=SECP256K1()),
        PlainSerializer(lambda curve: curve.name),
    ]

    @model_validator(mode="before")
    @classmethod
    def preprocess_elliptic_curve(cls, data: AnyT) -> AnyT:
        if not isinstance(data, dict):
            return data
        if not (crv := data.get("crv")):
            return data
        if not (ec_curve := cls.CURVE_TYPES.get(crv)):
            raise ValueError(f"Unknown/Unsupported curve: {crv}")
        data["crv"] = ec_curve
        return data

    def __json_repr__(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @staticmethod
    def b64url_encode_point(point: int) -> str:
        return to_base64url(point)

    @staticmethod
    def b6furl_decode_point(b64url_point: str) -> int:
        return from_base64url(b64url_point)


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
