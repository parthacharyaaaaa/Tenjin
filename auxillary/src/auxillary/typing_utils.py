from typing import Any, Generic, Protocol, TypeVar

from redis.typing import EncodableT, FieldT

__all__ = (
    "SupportsJSON",
    "SupportsCache",
    "SupportsMembershipCheck",
)

T = TypeVar("T", covariant=True)


class SupportsJSON(Protocol):
    def __json_repr__(self) -> dict[str, Any]: ...


class SupportsCache(Protocol):
    def __cache_repr__(self) -> dict[FieldT, EncodableT]: ...


class SupportsMembershipCheck(Generic[T], Protocol):
    def __contains__(self, o: object, /) -> bool: ...
