from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Hashable, Self, TypeVar

from redis.asyncio.client import Redis as AsyncRedis
from redis.client import Redis as SyncRedis
from redis.commands.core import AsyncScript, Script

KeyT = TypeVar("KeyT", bound=Hashable)


@dataclass(slots=True, frozen=True, init=False)
class RedisScriptRegistry:
    registered_script_hashes: MappingProxyType[Hashable, str]

    @classmethod
    async def async_initialize_script_registry(
        cls, redis_client: AsyncRedis, script_mapping: Mapping[KeyT, str]
    ) -> Self:
        async with redis_client.pipeline(transaction=True) as pipeline:
            for script in script_mapping.values():
                pipeline.register_script(script)
            results: list[AsyncScript] = await pipeline.execute()
        instance: Self = cls()
        instance._populate_script_registry(script_mapping.keys(), results)
        return instance

    @classmethod
    def sync_initialize_script_registry(
        cls, redis_client: SyncRedis, script_mapping: Mapping[KeyT, str]
    ) -> Self:
        with redis_client.pipeline(transaction=True) as pipeline:
            for script in script_mapping.values():
                pipeline.register_script(script)
            results: list[Script] = pipeline.execute()
        instance: Self = cls()
        instance._populate_script_registry(script_mapping.keys(), results)
        return instance

    def _populate_script_registry(
        self,
        script_names: Iterable[KeyT],
        results: Sequence[AsyncScript] | Sequence[Script],
    ) -> None:
        if not all(isinstance(i, (AsyncScript, Script)) for i in results):
            raise ValueError("Failed to register supplied scripts")
        object.__setattr__(
            self,
            "registered_scripts",
            MappingProxyType(dict(zip(script_names, (r.sha for r in results)))),
        )
