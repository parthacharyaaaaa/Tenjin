from dataclasses import dataclass
from typing import ClassVar, Mapping

from auxillary.data_structures.dto import AbstractResult

from resource_auxillary.cache import NAME_SEPERATOR


@dataclass(slots=True, init=False)
class AbstractDTO(AbstractResult):
    _counter_fields: ClassVar[tuple[str, ...]] = tuple()
    counter_fields_map: ClassVar[Mapping[str, str]] = {}

    def __init_subclass__(cls):
        cls.counter_fields_map = {
            i: NAME_SEPERATOR.join((cls.resource_name, i)) for i in cls._counter_fields
        }
