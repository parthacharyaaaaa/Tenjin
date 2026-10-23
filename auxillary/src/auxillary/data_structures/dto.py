from dataclasses import dataclass, fields
from datetime import UTC, datetime
from types import MappingProxyType, NoneType
from typing import Any, Callable, ClassVar, Final, LiteralString, Mapping, Self

import orjson
from redis.typing import EncodableT, FieldT
from sqlalchemy.orm import DeclarativeBase

type t_dto_casting_map = MappingProxyType[type, Callable[[Any], Any]]

NONETYPE_CACHE_SUBSTITUTE: Final[LiteralString] = "__NONETYPE__"


def _json_convert_datetime(d: datetime) -> str:
    return d.isoformat()


def _reverse_json_convert_datetime(d: str | int | float) -> datetime:
    if isinstance(d, str):
        return datetime.fromisoformat(d)
    if isinstance(d, (int, float)):
        return datetime.fromtimestamp(d, tz=UTC)
    raise ValueError("Unsupported format for datetime")


def _cache_convert_nonetype(n: NoneType) -> str:
    return NONETYPE_CACHE_SUBSTITUTE


def _reverse_cache_convert_list(l: str) -> list[Any]:
    converted = orjson.loads(l)
    if not isinstance(converted, list):
        raise ValueError(f"{l} not a list")
    return converted


def _reverse_cache_convert_dict(d: str) -> dict[str, Any]:
    converted = orjson.loads(d)
    if not isinstance(converted, dict):
        raise ValueError(f"{d} not a dictionary")
    return converted


JSON_TYPE_MAPPING: Final[t_dto_casting_map] = MappingProxyType(
    {
        datetime: _json_convert_datetime,
    }
)

REVERSE_JSON_TYPE_MAPPING: Final[t_dto_casting_map] = MappingProxyType(
    {datetime: _reverse_json_convert_datetime}
)

CACHE_TYPE_MAPPING: Final[t_dto_casting_map] = MappingProxyType(
    JSON_TYPE_MAPPING
    | {
        NoneType: _cache_convert_nonetype,
        bool: int,
        list: str,
        dict: str,
    }
)

REVERSE_CACHE_TYPE_MAPPING: Final[t_dto_casting_map] = MappingProxyType(
    REVERSE_JSON_TYPE_MAPPING
    | {
        bool: bool,
        list: _reverse_cache_convert_list,
        dict: _reverse_cache_convert_dict,
    }
)


@dataclass(slots=True, init=False)
class AbstractResult:
    resource_name: ClassVar[str]
    _fields: ClassVar[tuple[str, ...]] = tuple()

    def __init_subclass__(cls):
        cls._fields = tuple(f.name for f in fields(cls))
        if not hasattr(cls, "resource_name"):
            raise ValueError("Missing class variable: resource_name")

    @classmethod
    def construct_from_cache(cls, mapping: Mapping[str, Any], *args, **kwargs) -> Self:
        instance = cls()
        for field in fields(instance):
            setattr(
                instance,
                field.name,
                REVERSE_CACHE_TYPE_MAPPING.get(field.type, lambda x: x)(value)
                if (value := mapping[field.name]) != NONETYPE_CACHE_SUBSTITUTE
                else None,
            )
        return instance

    @classmethod
    def construct_from_orm(cls, obj: DeclarativeBase, *args, **kwargs) -> Self:
        instance = cls()

        for attribute in obj.__table__.columns.keys():
            if attribute in cls._fields:
                setattr(instance, attribute, getattr(obj, attribute))
            elif (stripped_attribute := attribute.strip("_")) in cls._fields:
                setattr(instance, stripped_attribute, getattr(obj, attribute))
        return instance

    def map_fields(
        self, casting_map: Mapping[type, Callable[[Any], Any]]
    ) -> dict[str, Any]:
        return {
            field.name: casting_map.get(type(getattr(self, field.name)), lambda x: x)(  # type: ignore
                getattr(self, field.name)
            )
            for field in fields(self)
            if not field.name.startswith("_")
        }

    def __json_repr__(self) -> dict[str, Any]:
        return self.map_fields(JSON_TYPE_MAPPING)

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        return {
            field.name: CACHE_TYPE_MAPPING.get(field.type, lambda x: x)(field_value)
            if (field_value := getattr(self, field.name)) is not None
            else NONETYPE_CACHE_SUBSTITUTE
            for field in fields(self)
            if not field.name.startswith("_")
        }
