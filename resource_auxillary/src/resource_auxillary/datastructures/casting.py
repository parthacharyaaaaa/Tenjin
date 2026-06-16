from datetime import datetime
from types import MappingProxyType
from typing import Any, Callable, Final, TypeVar

T = TypeVar("T")

type t_cast_mapping = MappingProxyType[type, Callable[[str], Any]]


def serialize_datetime(arg: str) -> datetime:
    return datetime.fromisoformat(arg)


def serialize_bool(arg: str) -> bool:
    return bool(int(arg))


def default_serializer(arg: str, type_callable: Callable[[str], T]) -> T:
    return type_callable(arg)


CAST_MAPPING: Final[t_cast_mapping] = MappingProxyType(
    {
        bool: serialize_bool,
        datetime: serialize_datetime,
    }
)
