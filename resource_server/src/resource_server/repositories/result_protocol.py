from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
from functools import lru_cache
from typing import Any, Mapping, Self

from redis.typing import FieldT, EncodableT

from sqlalchemy.orm import DeclarativeBase

from resource_auxillary.cache import CACHE_TYPE_MAPPING


@dataclass(slots=True, init=False)
class AbstractResult(ABC):

    # TODO: Add default implementation for construct_from_cache
    @classmethod
    @abstractmethod
    def construct_from_cache(cls, mapping: Mapping[str, Any]) -> Self: ...
    @classmethod
    @abstractmethod
    def construct_from_orm(cls, obj: DeclarativeBase, *args, **kwargs) -> Self: ...

    def __json_repr__(self) -> dict[str, Any]:
        return {
            field.name.strip("_"): getattr(self, field.name)
            for field in fields(self)
            if not field.name.startswith("_")
        }

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        return {
            field.name.strip("_"): CACHE_TYPE_MAPPING.get(field.type, lambda x: x)(
                getattr(self, field.name)  # type: ignore
            )
            for field in fields(self)
            if not field.name.startswith("_")
        }

    @lru_cache(maxsize=1)
    @abstractmethod
    @classmethod
    def get_counter_fields(cls) -> dict[str, str]: ...
