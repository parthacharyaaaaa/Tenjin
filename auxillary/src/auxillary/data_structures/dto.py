from typing import Annotated, Any, ClassVar, Mapping, Protocol, Self

import orjson
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict
from redis.typing import EncodableT, FieldT
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.util.typing import Final


class CastingFunction(Protocol):
    def __call__(self, arg: Any, *args, **kwargs) -> Any: ...


INT64_MIN: Final[int] = -(2**63)
INT64_MAX: Final[int] = 2**63 - 1

type int64 = Annotated[int, Field(ge=INT64_MIN, le=INT64_MAX)]
type uint64 = Annotated[int, Field(ge=0, le=INT64_MAX)]


class AbstractResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    resource_name: ClassVar[str]

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs):
        super().__pydantic_init_subclass__(**kwargs)
        resource_name = getattr(cls, "resource_name", None)
        if resource_name is None:
            raise ValueError("Missing class variable: resource_name")
        if not isinstance(resource_name, str):
            raise ValueError("resource_name class variable should be str instance")

    @classmethod
    def construct_from_cache(
        cls, mapping: Mapping[str, Any], /, *args, **kwargs
    ) -> Self:
        return cls.model_validate({k: orjson.loads(v) for k, v in mapping.items()})

    @classmethod
    def construct_from_orm(cls, obj: DeclarativeBase, /, *args, **kwargs) -> Self:
        return cls.model_validate(obj)

    def __json_repr__(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        return {k: orjson.dumps(v) for k, v in self.model_dump(mode="json").items()}
