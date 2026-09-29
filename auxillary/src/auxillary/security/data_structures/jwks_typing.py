from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, Protocol, Self

from cryptography.hazmat.primitives.asymmetric.ec import (
    EllipticCurve,
    EllipticCurvePublicNumbers,
)
from pydantic.main import IncEx

from auxillary.security.data_structures.jwks_enums import JWKKty, JWKUse


class SupportsJWK(Protocol):
    @property
    def kty(self) -> JWKKty: ...
    @property
    def use(self) -> Literal[JWKUse.SIG]: ...
    @property
    def alg(self) -> str: ...

    kid: str


class SupportsPydanticModelSerialization(Protocol):
    def model_dump(
        self,
        *,
        mode: str = "python",
        include: Mapping[int, bool | IncEx]
        | Mapping[str, bool | IncEx]
        | set[int]
        | set[str]
        | None = None,
        exclude: Mapping[int, bool | IncEx]
        | Mapping[str, bool | IncEx]
        | set[int]
        | set[str]
        | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        exclude_unset: bool = False,
        exclude_defaults: bool = False,
        exclude_none: bool = False,
        exclude_computed_fields: bool = False,
        round_trip: bool = False,
        warnings: Literal["error", "none", "warn"] | bool = True,
        fallback: Callable[[Any], Any] | None = None,
        serialize_as_any: bool = False,
        polymorphic_serialization: bool | None = None,
    ) -> dict[str, Any]: ...

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        extra: Literal["allow", "forbid", "ignore"] | None = None,
        from_attributes: bool | None = None,
        context: Any | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self: ...


class SupportsJWKSerialization(SupportsJWK, SupportsPydanticModelSerialization): ...


class SupportsEllipticCurveJWK(SupportsJWK):
    crv: type[EllipticCurve]
    public_memebrs: EllipticCurvePublicNumbers


class JWKS(Protocol):
    @property
    def keys(self) -> Sequence[SupportsJWKSerialization]: ...
