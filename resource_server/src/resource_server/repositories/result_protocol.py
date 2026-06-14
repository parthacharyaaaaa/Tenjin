from dataclasses import dataclass, fields
from typing import Any, ClassVar, Mapping, Self

from redis.typing import FieldT, EncodableT

from sqlalchemy.orm import DeclarativeBase

from resource_auxillary.cache import CACHE_TYPE_MAPPING, NAME_SEPERATOR


@dataclass(slots=True, init=False)
class AbstractResult:
    resource_name: ClassVar[str]
    _fields: ClassVar[tuple[str, ...]] = tuple()
    _counter_fields: ClassVar[tuple[str, ...]] = tuple()
    counter_fields_map: ClassVar[Mapping[str, str]] = {}

    def __init_subclass__(cls):
        cls._fields = tuple(f.name for f in fields(cls))
        cls._counter_fields_map = {
            i: NAME_SEPERATOR.join((cls.resource_name, i)) for i in cls._counter_fields
        }
        if not hasattr(cls, "resource_name"):
            raise ValueError(f"Missing class variable: resource_name")

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

    def __json_repr__(self) -> dict[str, Any]:
        return {
            field.name.strip("_"): getattr(self, field.name)
            for field in fields(self)
            if not field.name.startswith("_")
        }

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        return {
            field.name.strip("_"): CACHE_TYPE_MAPPING.get(field.type, lambda x: x)(  # type: ignore
                getattr(self, field.name)
            )
            for field in fields(self)
            if not field.name.startswith("_")
        }
