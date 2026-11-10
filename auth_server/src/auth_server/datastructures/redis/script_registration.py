from types import MappingProxyType
from typing import Final, LiteralString

from auth_server.datastructures.redis.script_names import RedisScriptName
from auth_server.datastructures.redis.scripts import (
    CONDITIONAL_HASH_ENTRY_EVICTION_SCRIPT,
)

RAW_SCRIPT_REGISTRY: Final[MappingProxyType[RedisScriptName, LiteralString]] = (
    MappingProxyType(
        {
            RedisScriptName.CONDITIONAL_HASH_ENTRY_EVICTION_SCRIPT: CONDITIONAL_HASH_ENTRY_EVICTION_SCRIPT
        }
    )
)
