from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Any, Mapping, Self

from redis.typing import FieldT, EncodableT

from sqlalchemy.orm import DeclarativeBase


class AbstractResult(ABC):
    @classmethod
    @abstractmethod
    def construct_from_cache(cls, mapping: Mapping[str, Any]) -> Self: ...
    @classmethod
    @abstractmethod
    def construct_from_orm(cls, obj: DeclarativeBase, *args, **kwargs) -> Self: ...

    @abstractmethod
    def __json_repr__(self) -> dict[str, Any]: ...
    @abstractmethod
    def __cache_repr__(self) -> dict[FieldT, EncodableT]: ...

    @lru_cache(maxsize=1)
    @abstractmethod
    @classmethod
    def get_counter_fields(cls) -> dict[str, str]: ...
