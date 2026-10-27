from typing import ClassVar, LiteralString, Mapping

from auxillary.data_structures.dto import AbstractResult
from sqlalchemy.util.typing import Final

_RESOURCE_NAME_PLACEHOLDER_SENTINEL: Final[LiteralString] = "__NONE__"


class AbstractDTO(AbstractResult):
    resource_name: ClassVar[str] = _RESOURCE_NAME_PLACEHOLDER_SENTINEL
    _counter_fields: ClassVar[tuple[str, ...]] = tuple()
    counter_fields_map: ClassVar[Mapping[str, str]] = {}

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs):
        super().__pydantic_init_subclass__(**kwargs)
        if cls.resource_name == _RESOURCE_NAME_PLACEHOLDER_SENTINEL:
            raise ValueError("Missing class variable: resource_name")
