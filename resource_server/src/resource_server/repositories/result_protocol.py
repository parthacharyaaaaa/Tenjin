from dataclasses import dataclass, fields
from typing import ClassVar, Mapping

from redis.typing import FieldT, EncodableT

from auxillary.data_structures.dto import AbstractResult

from resource_auxillary.cache import NAME_SEPERATOR, CACHE_TYPE_MAPPING


@dataclass(slots=True, init=False)
class AbstractDTO(AbstractResult):
    _counter_fields: ClassVar[tuple[str, ...]] = tuple()
    counter_fields_map: ClassVar[Mapping[str, str]] = {}

    def __init_subclass__(cls):
        cls.counter_fields_map = {
            i: NAME_SEPERATOR.join((cls.resource_name, i)) for i in cls._counter_fields
        }

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        return {
            field.name.strip("_"): CACHE_TYPE_MAPPING.get(field.type, lambda x: x)(  # type: ignore
                getattr(self, field.name)
            )
            for field in fields(self)
            if not field.name.startswith("_")
        }
