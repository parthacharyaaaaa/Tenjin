from abc import abstractmethod
from dataclasses import dataclass, fields
from typing import Any, ClassVar, Mapping, Self

from redis.typing import FieldT, EncodableT

from sqlalchemy.orm import DeclarativeBase


@dataclass(slots=True, init=False)
class AbstractResult:
    resource_name: ClassVar[str]
    _fields: ClassVar[tuple[str, ...]] = tuple()

    def __init_subclass__(cls):
        cls._fields = tuple(f.name for f in fields(cls))
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

    @abstractmethod
    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        raise NotImplementedError()
