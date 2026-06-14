from datetime import datetime
from types import MappingProxyType, NoneType
from typing import Any, Callable, Final

from resource_auxillary.strings import NAME_SEPERATOR, Action

type t_cache_casting_map = MappingProxyType[type, Callable[[Any], Any]]

CACHE_TYPE_MAPPING: Final[t_cache_casting_map] = MappingProxyType(
    {
        NoneType: lambda _: "",
        bool: int,
        datetime: lambda x: x.isoformat(),
        list: str,
        dict: str,
    }
)


def create_intent_flag(
    entity: str,
    action: str,
    user_identifier: str,
    resource_identifier: str,
) -> str:
    return NAME_SEPERATOR.join((entity, action, user_identifier, resource_identifier))


def derive_cache_key(resource_name: str, identifier: str | int) -> str:
    return NAME_SEPERATOR.join((resource_name, str(identifier)))


def derive_hashmap_name(resource_name: str, field: str) -> str:
    return NAME_SEPERATOR.join((resource_name, field))
