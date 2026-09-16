from dataclasses import dataclass, fields
from datetime import datetime
from types import MappingProxyType, NoneType
from typing import Any, Callable, ClassVar, Final, Mapping, Self

from redis.typing import EncodableT, FieldT
from sqlalchemy.orm import DeclarativeBase

type t_dto_casting_map = MappingProxyType[type, Callable[[Any], Any]]

JSON_TYPE_MAPPING: Final[t_dto_casting_map] = MappingProxyType(
    {
        datetime: lambda x: x.isoformat(),
    }
)

CACHE_TYPE_MAPPING: Final[t_dto_casting_map] = MappingProxyType(
    JSON_TYPE_MAPPING
    | {
        NoneType: lambda _: "",
        bool: int,
        list: str,
        dict: str,
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

        for k, v in mapping.items():
            if k in cls._fields:
                setattr(instance, k, v)
        return instance

    @classmethod
    def construct_from_orm(cls, obj: DeclarativeBase, *args, **kwargs) -> Self:
        instance = cls()

        for attribute in obj.__table__.columns.keys():
            if (dataclass_attribute := attribute.strip("_")) in cls._fields:
                setattr(instance, dataclass_attribute, getattr(obj, attribute))
        return instance

    def map_fields(
        self, casting_map: Mapping[type, Callable[[Any], Any]]
    ) -> dict[str, Any]:
        return {
            field.name.strip("_"): casting_map.get(field.type, lambda x: x)(  # type: ignore
                getattr(self, field.name)
            )
            for field in fields(self)
            if not field.name.startswith("_")
        }

    def __json_repr__(self) -> dict[str, Any]:
        return self.map_fields(JSON_TYPE_MAPPING)

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        return self.map_fields(CACHE_TYPE_MAPPING)  # type: ignore
