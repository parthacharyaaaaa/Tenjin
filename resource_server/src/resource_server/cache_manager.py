import asyncio
from dataclasses import dataclass
from random import randint
import time
from typing import Any, TypeVar, ClassVar, Coroutine, Final, Literal, Mapping, Sequence

import orjson

from redis import RedisError
from redis.asyncio.client import Redis

from auxillary.typing_utils import SupportsAsyncRedis

from resource_server.config.sub_config import CacheConfig
from resource_server.utils.singleton import SingletonMetaclass
from resource_server.datastructures.exceptions import (
    CacheCoherenceException,
    ResourceNotFoundException,
    ResourceDeletedException,
    OperationUnderwayException,
)
from resource_server.repositories.result_protocol import AbstractResult

from resource_auxillary.strings import IntentFlag
from resource_auxillary.cache import (
    derive_cache_key,
    derive_deletion_intent_flag,
    create_creation_intent_flag,
)

DTO_T = TypeVar("DTO_T", bound=AbstractResult)

type database_fallback_callable = Coroutine[Any, Any, AbstractResult | None]


@dataclass(init=False, slots=True, weakref_slot=True)
class CacheManager(metaclass=SingletonMetaclass):

    redis_client: SupportsAsyncRedis
    cache_config: CacheConfig

    allowed_intents: ClassVar[frozenset[str]] = frozenset(
        [
            IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
            IntentFlag.RESOURCE_CREATION_PENDING_ALT_FLAG,
            IntentFlag.RESOURCE_CREATION_PENDING_FLAG,
        ]
    )

    def __init__(self, redis: Redis, cache_config: CacheConfig) -> None:
        self.redis_client = redis  # type: ignore
        self.cache_config = cache_config

    @staticmethod
    def derive_lock_key(*args: str) -> str:
        return ":".join(("lock", *args))

    async def set_negative_string(self, key: str, *, ttl: int | None = None) -> None:
        ttl = ttl or self.cache_config.TTL_EPHEMERAL
        await self.redis_client.set(key, self.cache_config.NF_SENTINEL_KEY, ex=ttl)

    async def set_negative_mapping(self, key: str, *, ttl: int | None = None) -> None:
        ttl = ttl or self.cache_config.TTL_EPHEMERAL
        await self.hset_with_ttl(key, self.cache_config.NF_MAPPING, ttl)

    async def hset_with_ttl(
        self, name: str, mapping: dict, ttl: int, transaction: bool = False
    ):
        async with self.redis_client.pipeline(transaction) as pipe:
            pipe.hset(name=name, mapping=mapping)
            pipe.expire(name=name, time=ttl)
            await pipe.execute()

    async def batch_hset_with_ttl(
        self,
        names: Sequence[str],
        mappings: Sequence[dict],
        ttl: int,
        transaction: bool = True,
    ):
        if len(names) != len(mappings):
            raise ValueError("Names and mappings do not match")

        async with self.redis_client.pipeline(transaction) as pipe:
            for idx, mapping in enumerate(mappings):
                pipe.hset(name=names[idx], mapping=mapping)
                pipe.expire(name=names[idx], time=ttl)
            await pipe.execute()

    async def fetch_global_counters(
        self, hashmaps: Sequence[str], identifiers: Sequence[str]
    ) -> dict[str, list[int | None]]:
        async with self.redis_client.pipeline(transaction=False) as pipe:
            for hashmap in hashmaps:
                for identifer in identifiers:
                    pipe.hget(hashmap, identifer)
            counters: list[int | None] = [
                res if res is None else int(res) for res in await pipe.execute()
            ]
        counter_mapping: dict[str, list[int | None]] = {}
        step: int = len(identifiers)

        for idx, hashmap in enumerate(hashmaps):
            counter_mapping[hashmap] = counters[step * idx : step * (idx + 1)]
        return counter_mapping

    async def resource_existence_cache_precheck(
        self,
        identifier: str,
        resource_name: str,
        cache_key: str | None = None,
        deletion_flag_key: str | None = None,
    ) -> dict[str, Any]:
        """Generic check for a resource's existence in cache. Raises appropriate HTTP exception if non-existence is guarenteed
        Args:
            identifier: Unique identifier (Typically PK) of resource
            resource_name: Cache key for this resource
            deletion_flag_key: Cache key for deletion flag for this resource

        Returns:
            resource_mapping (dict[str, Any]) on cache hit"""
        cache_key = cache_key or f"{resource_name}:{identifier}"
        deletion_flag_key = deletion_flag_key or f"delete:{cache_key}"

        async with self.redis_client.pipeline() as pipe:
            pipe.hgetall(cache_key)
            pipe.get(deletion_flag_key)
            resource_mapping, deletion_intent = await pipe.execute()
        if deletion_intent:
            raise ResourceDeletedException("This resource was just deleted")
        if resource_mapping and self.cache_config.NF_SENTINEL_KEY in resource_mapping:
            await self.hset_with_ttl(
                cache_key, resource_mapping, self.cache_config.TTL_EPHEMERAL
            )
            raise ResourceNotFoundException(
                f"No {resource_name} with ID {identifier} found"
            )

        return resource_mapping

    async def resource_cache_precheck(
        self,
        identifier: str,
        resource: str,
        action_flag: str,
        lock_name: str,
        *,
        conflicting_intent: str | None = None,
        allow_deletion: bool = False,
    ) -> tuple[dict | None, str | None, bool]:
        """
        Consult cache and perform a check on a given resource to try to validate
        the request through cache and minimize DB lookups.
        Although this cannot guarantee resource validity,
        if it is found to be invalid through cache,
        an appropriate exception is raised.
        Args:
            identifier: Unique identifier for resource (Typically PK)
            cache_key: cache key for this resource
            deletion_intent_flag: Deletion flag name for this resource
            action_flag: Flag name to check for latest user intent for this resource
            lock_name: Name of lock for this action
            conflicting_intent: If specified, latest user intent is checked against
            this value for early rejection

        Returns:
            resource_mapping (dict), latest_intent (str), deletion intent (bool)
        """
        cache_key: Final[str] = self.derive_cache_key(resource, identifier)
        async with self.redis_client.pipeline() as pipe:
            pipe.hgetall(cache_key)
            pipe.get(self.derive_deletion_intent(resource, identifier))
            pipe.get(self.derive_intent_key(action_flag, identifier, resource))
            pipe.get(lock_name)

            resource_mapping, deletion_intent, latest_intent, lock = (
                await pipe.execute()
            )

        if deletion_intent and not allow_deletion:
            raise ResourceDeletedException(
                "This resource has been permanently deleted, and will soon be unavailable"
            )
        if (
            resource_mapping and self.cache_config.NF_SENTINEL_KEY in resource_mapping
        ) and not deletion_intent:  # Non-existence written in cache
            await self.hset_with_ttl(
                cache_key, resource_mapping, self.cache_config.TTL_EPHEMERAL
            )  # Reannounce non-existence
            raise ResourceNotFoundException(
                f"No {resource} with ID {identifier} could be found (Never existed, or deleted)"
            )

        if lock:  # Stop race condition early
            raise OperationUnderwayException(
                "Another worker is processing this exact request at the moment"
            )
        if (latest_intent and not conflicting_intent) or (
            latest_intent and conflicting_intent and latest_intent == conflicting_intent
        ):  # Stop duplicate requests
            raise OperationUnderwayException(
                f"This action for resource {identifier} has already been requested"
            )

        return (
            resource_mapping,
            latest_intent,
            bool(deletion_intent),
        )

    async def check_negative_entry(
        self,
        cache_key: str,
        cache_entry: dict | str,
        *,
        dtype: Literal["mapping", "string"] = "mapping",
    ) -> bool:
        if dtype == "mapping" and self.cache_config.NF_SENTINEL_KEY in cache_entry:
            async with self.redis_client.pipeline() as pipe:
                pipe.hset(cache_key, mapping=self.cache_config.NF_MAPPING)
                pipe.expire(cache_key, self.cache_config.TTL_EPHEMERAL)
                await pipe.execute()
            return True

        elif dtype == "string" and cache_entry == self.cache_config.NF_SENTINEL_KEY:
            await self.redis_client.set(
                cache_key,
                self.cache_config.NF_SENTINEL_KEY,
                self.cache_config.TTL_EPHEMERAL,
            )
            return True
        return False

    async def _primitive_get_from_cache(
        self, cache_key: str, *, dtype: Literal["mapping", "string"] = "mapping"
    ) -> tuple[dict | str, int] | None:
        async with self.redis_client.pipeline(transaction=False) as pipe:
            if dtype == "mapping":
                pipe.hgetall(cache_key)
            else:
                pipe.get(cache_key)
            pipe.ttl(cache_key)
            cache_entry, ttl = await pipe.execute()

        if not cache_entry:
            return None
        return cache_entry, ttl

    async def _fetch_from_cache(
        self, cache_key: str, *, dtype: Literal["mapping", "string"] = "mapping"
    ) -> dict[str, Any] | None:
        res = await self._primitive_get_from_cache(cache_key, dtype=dtype)
        if not res:
            return None

        cache_entry, ttl = res
        if await self.check_negative_entry(cache_key, cache_entry, dtype=dtype):
            return self.cache_config.NF_MAPPING

        # Cache hit, and resource actually exists
        await self.redis_client.expire(
            cache_key,
            min(self.cache_config.TTL_CAP, self.cache_config.TTL_PROMOTION + ttl),
        )

        if isinstance(cache_entry, dict):
            return cache_entry
        return orjson.loads(cache_entry)

    async def distributed_get_or_load(
        self,
        key: str,
        fallback_coroutine: database_fallback_callable,
        return_dto: type[DTO_T],
        *,
        fetch_dtype: Literal["mapping", "string"] = "mapping",
    ) -> DTO_T | None:
        result = await self._fetch_from_cache(key, dtype=fetch_dtype)
        # Cache hit, either negative entry or actual entry found
        if result:
            if self.cache_config.NF_SENTINEL_KEY in result:
                return None
            return return_dto.construct_from_cache(result)

        # Upon cache miss, elect a leader to actually talk to DB
        lock_name: Final[str] = self.derive_lock_key(key)
        for leader_attempt in range(self.cache_config.FETCH_MAX_RETRIES):
            leader: bool = False
            leader = bool(
                await self.redis_client.set(
                    lock_name, time.time(), px=self.cache_config.TTL_FETCH_LOCK, nx=True
                )
            )
            if leader:
                try:
                    result_dto: AbstractResult | None = await fallback_coroutine
                    if not result_dto:
                        if fetch_dtype == "mapping":
                            await self.set_negative_mapping(key)
                        else:
                            await self.set_negative_string(key)
                        return None
                    await self.hset_with_ttl(
                        key, cache_repr(result_dto), self.cache_config.TTL_STRONG
                    )
                    return result_dto  # type: ignore[reportReturnType]
                finally:
                    await self.redis_client.delete(lock_name)
            else:
                for i in range(1, self.cache_config.FETCH_WAITING_MAX_INTERVALS + 1):
                    if await self.redis_client.get(lock_name):
                        await asyncio.sleep(
                            self.cache_config.FETCH_WAITING_INITIAL_INTERVAL
                            * randint(1, self.cache_config.FETCH_WAITING_JITTER)
                            ** self.cache_config.FETCH_WAITING_EXPONENT
                        )
                        continue

                    res = await self._primitive_get_from_cache(key, dtype=fetch_dtype)
                    # Leader announced negative entry
                    if res and self.cache_config.NF_SENTINEL_KEY in res:
                        return None
                    # Leader failed, try again
                    if not res:
                        break
                    cache_entry = res[0]
                    if isinstance(cache_entry, dict):
                        return return_dto.construct_from_cache(cache_entry)
                    return return_dto.construct_from_cache(orjson.loads(cache_entry))

        raise CacheCoherenceException(f"Failed to fetch {key}")

    async def fetch_group_resources(
        self,
        group_key: str,
        *,
        element_dtype: Literal["mapping", "string"] = "mapping",
    ) -> tuple[tuple[Any] | None, bool, str | None]:
        """
        Fetches all values for keys stored in a Redis iterable
        (list, set, or sorted set) for cursor based pagination.
        If any key is missing from the cache,
        the function returns `None` to indicate a cache miss.
        Args:
            group_key: The Redis key pointing to a collection of resource keys.
            element_dtype: The expected data type of each individual resource key
            in the group (used for deserialization).

        Returns:
            tuple: A tuple of values corresponding to each key in the group,
            boolean indicating end of pagination, value of next cursor
        """
        keys: list[str] = await self.redis_client.lrange(group_key, 0, -1)

        if not keys or self.cache_config.NF_SENTINEL_KEY in keys:
            return None, True, None

        cursor: str | None = None
        end: bool = False
        removed_entries: list[str] = []
        for idx, entry in enumerate(keys):
            if entry.startswith("cursor:"):
                # Fetch next cursor for pagination if available
                cursor = entry.split(":")[1]
                removed_entries.append(entry)
            elif entry.startswith("end:"):
                removed_entries.append(entry)
                end = False if entry.split(":")[1].lower() == "false" else True

        for entry in removed_entries:
            keys.remove(entry)  # Remove cursor and end keys from group keys

        resources: list[dict[str, Any] | str] = []
        async with self.redis_client.pipeline() as pipe:
            for key in keys:
                if element_dtype == "mapping":
                    pipe.hgetall(key)
                else:
                    pipe.get(key)
            resources = await pipe.execute()

        # Account for sentinel mappings
        if element_dtype == "mapping":
            return (
                tuple(
                    map(
                        lambda resource: (
                            None
                            if self.cache_config.NF_SENTINEL_KEY in resource
                            else resource
                        ),
                        resources,
                    )
                ),
                end,
                cursor,
            )

        return (
            tuple(
                map(
                    lambda resource: (
                        None
                        if resource == self.cache_config.NF_SENTINEL_KEY
                        else orjson.loads(resource)  # type: ignore[reportArgumentType]
                    ),
                    resources,
                )
            ),
            end,
            cursor,
        )

    async def promote_group_ttl(
        self,
        group_key: str,
        *,
        promotion_ttl: int | None = None,
        max_ttl: int | None = None,
    ) -> None:
        max_ttl = max_ttl or self.cache_config.TTL_CAP
        promotion_ttl = promotion_ttl or self.cache_config.TTL_PROMOTION
        keys: list[str] = await self.redis_client.lrange(group_key, 0, -1)

        if not keys:
            return

        # Fetch TTLs
        async with self.redis_client.pipeline() as pipe:
            pipe.ttl(group_key)
            for key in keys:
                pipe.ttl(key)

            ttl_list: list[int] = await pipe.execute()

        # Promote TTls
        async with self.redis_client.pipeline() as pipe:
            pipe.expire(group_key, min(max_ttl, ttl_list[0] + promotion_ttl))

            for idx, key in enumerate(keys, start=1):
                pipe.expire(key, min(max_ttl, ttl_list[idx] + promotion_ttl))
            await pipe.execute()

    async def cache_grouped_resource(
        self,
        group_key: str,
        resource_type: str,
        resources: Mapping[str | int, dict],
        weak_ttl: int,
        strong_ttl: int,
        cursor: str,
        end: bool,
        *,
        member_dtype: Literal["mapping", "string"] = "mapping",
    ) -> None:
        member_key_template: str = resource_type + ":{}"

        async with self.redis_client.pipeline() as pipe:
            pipe.expire(group_key, weak_ttl)
            for resourceID, resourceMapping in resources.items():
                key_name: str = member_key_template.format(resourceID)
                pipe.rpush(group_key, key_name)  # Push key name for resource into list

                # Cache individual resource separately
                if member_dtype == "mapping":
                    pipe.hset(key_name, mapping=resourceMapping)
                else:
                    pipe.set(key_name, orjson.dumps(resourceMapping).decode("utf-8"))
                pipe.expire(key_name, strong_ttl)

            pipe.rpush(group_key, f"cursor:{cursor}")
            pipe.rpush(group_key, f"end:{end}")
            await pipe.execute()
