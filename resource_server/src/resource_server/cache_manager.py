import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from random import randint
import time
from typing import (
    Any,
    Callable,
    LiteralString,
    TypeVar,
    ClassVar,
    Coroutine,
    Final,
    Literal,
    Mapping,
    Sequence,
)

import orjson

from redis.asyncio.client import Redis, Pipeline

from auxillary.typing_utils import SupportsAsyncRedis, SupportsCache
from auxillary.utils import cache_repr

from resource_server.config.sub_config import CacheConfig
from auxillary.singleton import SingletonMetaclass
from resource_server.datastructures.exceptions import (
    CacheCoherenceException,
    ConflictingIntentException,
    DuplicateRequestException,
)
from resource_server.repositories.result_protocol import AbstractResult

from resource_auxillary.strings import Action, IntentFlag, NAME_SEPERATOR
from resource_auxillary.cache import create_intent_flag

DTO_T = TypeVar("DTO_T", bound=AbstractResult)

type database_fallback_callable = Callable[
    [], Coroutine[Any, Any, AbstractResult | None]
]

type pagination_database_fallback_callable = Callable[
    [], Coroutine[Any, Any, Sequence[AbstractResult]]
]


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

    PAGINATION_SUB_KEY_SEPERATOR: ClassVar[LiteralString] = "-"
    CURSOR_UNDERIVABLE_SENTINEL: ClassVar[LiteralString] = "X"
    PAGINATION_VERSION_MAP: ClassVar[LiteralString] = "pagination_versions"
    MAX_CACHE_VERSION: ClassVar[int] = 64

    def __init__(self, redis: Redis, cache_config: CacheConfig) -> None:
        self.redis_client = redis  # type: ignore
        self.cache_config = cache_config

    @staticmethod
    def derive_lock_key(*args: str) -> str:
        return NAME_SEPERATOR.join(("lock", *args))

    async def derive_pagination_key(
        self,
        resource_name: str,
        cursor: int = 0,
        *args: str,
    ) -> str:
        version: str = (
            await self.redis_client.hget(self.PAGINATION_VERSION_MAP, resource_name)
            or "0"
        )
        return NAME_SEPERATOR.join((version, resource_name, str(cursor), *args))

    @staticmethod
    def derive_resource_from_pagination_key(key: str) -> str:
        return key.split(NAME_SEPERATOR)[0]

    @staticmethod
    def derive_cursor_from_pagination_key(key: str) -> str:
        return key.split(NAME_SEPERATOR)[2]

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
        self,
        cache_key: str,
        counter_fields: Mapping[str, str],
        *,
        dtype: Literal["mapping", "string"] = "mapping",
    ) -> tuple[dict[str, Any], int] | None:
        async with self.redis_client.pipeline(transaction=False) as pipe:
            if dtype == "mapping":
                pipe.hgetall(cache_key)
            else:
                pipe.get(cache_key)
            pipe.ttl(cache_key)

            for map_name in counter_fields.values():
                pipe.hget(map_name, cache_key)

            cache_entry, ttl, *counters = await pipe.execute()

        if not cache_entry:
            return None

        if isinstance(cache_entry, dict):
            for idx, field in enumerate(counter_fields.keys()):
                cache_entry[field] += int(counters[idx] or 0)
            return cache_entry, ttl

        materialized_entry: dict[str, Any] = orjson.loads(cache_entry)
        for idx, field in enumerate(counter_fields.keys()):
            materialized_entry[field] += int(counters[idx] or 0)
        return materialized_entry, ttl

    async def _primitive_pagination_get_from_cache(
        self,
        page_key: str,
        counter_fields: Mapping[str, str],
        *,
        dtype: Literal["mapping", "string"] = "mapping",
    ) -> tuple[int, list[str], list[tuple[dict[str, Any] | None, int]], str]:
        keys: list[str] = await self.redis_client.lrange(page_key, 0, -1)
        cursor: str = keys.pop(-1)
        async with self.redis_client.pipeline(transaction=False) as pipe:
            pipe.ttl(page_key)
            for key in keys:
                if dtype == "mapping":
                    pipe.hgetall(key)
                else:
                    pipe.get(key)
                pipe.ttl(key)
                for map_name in counter_fields.values():
                    pipe.hget(map_name, key)

            page_ttl, *results = await pipe.execute()

        result_collections: list[tuple[dict[str, Any] | None, int]] = []
        for i in range(0, len(results), 2 + len(counter_fields)):
            member_entry, member_ttl, *member_counters = results[
                i : i + 2 + len(counter_fields)
            ]
            if (
                member_entry == self.cache_config.NF_MAPPING or member_ttl == -2
            ):  # TTL for non-existent keys
                result_collections.append((None, member_ttl))
                continue

            elif isinstance(member_entry, dict):
                for idx, field in enumerate(counter_fields.keys()):
                    member_entry[field] += int(member_counters[idx] or 0)
                    result_collections.append((member_entry, member_ttl))
            else:
                materialized_entry: dict[str, Any] = orjson.loads(member_entry)  # type: ignore[reportArgumentType]
                for idx, field in enumerate(counter_fields.keys()):
                    materialized_entry[field] += int(member_counters[idx] or 0)
                    result_collections.append((materialized_entry, member_ttl))

        return page_ttl, keys, result_collections, cursor

    async def _fetch_from_cache(
        self,
        cache_key: str,
        counter_fields: Mapping[str, str] | None,
        *,
        dtype: Literal["mapping", "string"] = "mapping",
    ) -> dict[str, Any] | None:
        res = await self._primitive_get_from_cache(
            cache_key, counter_fields or {}, dtype=dtype
        )
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

        return cache_entry

    async def distributed_get_or_load(
        self,
        key: str,
        fallback_coroutine: database_fallback_callable,
        return_dto: type[DTO_T],
        *,
        fetch_dtype: Literal["mapping", "string"] = "mapping",
    ) -> DTO_T | None:
        result = await self._fetch_from_cache(
            key, return_dto.counter_fields_map, dtype=fetch_dtype
        )
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
                    result_dto: AbstractResult | None = await fallback_coroutine()
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
                            * randint(
                                1, self.cache_config.FETCH_WAITING_JITTER
                            )  # nosec
                            ** self.cache_config.FETCH_WAITING_EXPONENT
                        )
                        continue

                    res = await self._primitive_get_from_cache(
                        key, return_dto.counter_fields_map, dtype=fetch_dtype
                    )
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

    async def fetch_indicators(
        self,
        user_identifier: str,
        resource_identifier: str,
        resource_name: str,
        action: Action,
    ) -> tuple[str | None, IntentFlag | None]:
        """
        Returns:
            (tuple[str | None, tuple[IntentFlag, str] | None]):
            Active lock for operation, if found.
            Active intent, if found

        """
        intent: str = create_intent_flag(
            resource_name, action, user_identifier, resource_identifier
        )
        lock_name: str = self.derive_lock_key(
            resource_name, action.value, user_identifier, resource_identifier
        )

        async with self.redis_client.pipeline() as pipe:
            pipe.get(lock_name)
            pipe.get(intent)
            lock, intent = await pipe.execute()
        intent_value, *_ = intent.split(NAME_SEPERATOR)
        return lock, IntentFlag(intent_value) if intent_value else None

    @asynccontextmanager
    async def guard_action(
        self,
        user_identifier: str | int,
        resource_identifier: str | int,
        resource_name: str,
        action: Action,
        *,
        lock_conflict_message: str | None = None,
        conflicting_intent: IntentFlag | None = None,
        intent_conflict_message: str | None = None,
    ):
        user_identifier = str(user_identifier)
        resource_identifier = str(resource_identifier)

        intent: str = create_intent_flag(
            resource_name, action, user_identifier, resource_identifier
        )
        lock_name: str = self.derive_lock_key(
            resource_name, action.value, user_identifier, resource_identifier
        )
        try:
            async with self.redis_client.pipeline() as pipe:
                pipe.set(lock_name, 1, nx=True, px=self.cache_config.TTL_FETCH_LOCK)
                pipe.get(intent)
                lock_set, intent = await pipe.execute()
            if not lock_set:
                raise DuplicateRequestException(
                    lock_conflict_message or "Detected duplicate request"
                )
            if not intent:
                yield None
            intent_value: str = intent.split(NAME_SEPERATOR)[0]

            if conflicting_intent == intent_value:
                raise ConflictingIntentException(
                    intent_conflict_message or "Operation already performed"
                )
            yield intent_value
        finally:
            await self.redis_client.delete(lock_name)

    async def set_intent(
        self,
        intent_id: str,
        user_identifier: str,
        resource_identifier: str,
        resource_name: str,
        action: Action,
        intent_flag: IntentFlag,
        *,
        ttl: int | None = None,
    ):
        intent: str = create_intent_flag(
            resource_name, action, user_identifier, resource_identifier
        )
        await self.redis_client.set(
            intent,
            NAME_SEPERATOR.join((intent_flag, intent_id)),
            ex=ttl or self.cache_config.TTL_STRONGEST,
        )

    async def _fetch_paginated_resources(
        self,
        page_key: str,
        return_dto: type[DTO_T],
        *,
        element_dtype: Literal["mapping", "string"] = "mapping",
    ) -> tuple[tuple[DTO_T | None, ...] | None, str | None]:
        page_ttl, keys, paginated_entries, cursor = (
            await self._primitive_pagination_get_from_cache(
                page_key, return_dto.counter_fields_map, dtype=element_dtype
            )
        )

        if all(i[0] for i in paginated_entries):
            # Cached page clean
            await self._promote_paginated_result(
                page_key, page_ttl, keys, [i[1] for i in paginated_entries]
            )
        else:
            # Cached page dirty
            await self.update_cache_version(
                self.derive_resource_from_pagination_key(page_key)
            )

        return (
            tuple(
                map(
                    lambda x: return_dto.construct_from_cache(x[0]) if x[0] else None,
                    paginated_entries,
                )
            ),
            cursor,
        )

    async def distributed_pagination_get_or_load(
        self,
        page_key: str,
        fallback_coroutine: pagination_database_fallback_callable,
        return_dto: type[DTO_T],
        *,
        member_identifier: str = "id_",
        fetch_dtype: Literal["mapping", "string"] = "mapping",
    ) -> tuple[list[DTO_T], str | None]:
        cached_results, next_cursor = await self._fetch_paginated_resources(
            page_key, return_dto, element_dtype=fetch_dtype
        )

        if cached_results and all(cached_results):
            return list(cached_results), next_cursor  # type: ignore[reportReturnType]

        # Upon cache miss, elect a leader to actually talk to DB
        lock_name: Final[str] = self.derive_lock_key(page_key)
        for leader_attempt in range(self.cache_config.FETCH_MAX_RETRIES):
            leader: bool = False
            leader = bool(
                await self.redis_client.set(
                    lock_name, time.time(), px=self.cache_config.TTL_FETCH_LOCK, nx=True
                )
            )
            if leader:
                try:
                    results: list[AbstractResult] = list(await fallback_coroutine())
                    await self.cache_grouped_resource(
                        page_key,
                        {getattr(i, member_identifier): i for i in results},
                        self.derive_cursor_from_pagination_key(page_key),
                    )
                    return results, self.CURSOR_UNDERIVABLE_SENTINEL  # type: ignore[reportReturnType]
                finally:
                    await self.redis_client.delete(lock_name)
            else:
                for i in range(1, self.cache_config.FETCH_WAITING_MAX_INTERVALS + 1):
                    if await self.redis_client.get(lock_name):
                        await asyncio.sleep(
                            self.cache_config.FETCH_WAITING_INITIAL_INTERVAL
                            * randint(
                                1, self.cache_config.FETCH_WAITING_JITTER
                            )  # nosec
                            ** self.cache_config.FETCH_WAITING_EXPONENT
                        )
                        continue

                    *_, res, _ = await self._primitive_pagination_get_from_cache(
                        page_key, return_dto.counter_fields_map, dtype=fetch_dtype
                    )
                    # Leader failed, try again
                    if not res:
                        break
                    return (
                        list(
                            map(
                                lambda x: (
                                    return_dto.construct_from_cache(x) if x else None
                                ),
                                (r[0] for r in res),
                            )
                        ),
                        next_cursor,
                    )

        raise CacheCoherenceException(f"Failed to fetch {page_key}")

    async def _promote_paginated_result(
        self,
        page_key: str,
        page_ttl: int,
        member_keys: list[str],
        member_ttls: list[int],
    ) -> None:
        # Promote TTls
        async with self.redis_client.pipeline() as pipe:
            pipe.expire(
                page_key,
                min(
                    self.cache_config.TTL_CAP,
                    page_ttl + self.cache_config.TTL_PROMOTION,
                ),
            )

            for idx, key in enumerate(member_keys):
                pipe.expire(
                    key,
                    min(
                        self.cache_config.TTL_CAP,
                        member_ttls[idx] + self.cache_config.TTL_PROMOTION,
                    ),
                )
            await pipe.execute()

    async def cache_grouped_resource(
        self,
        page_key: str,
        resources: Mapping[str, SupportsCache],
        cursor: str | None = None,
        *,
        member_dtype: Literal["mapping", "string"] = "mapping",
    ) -> None:
        async with self.redis_client.pipeline(transaction=True) as pipe:
            pipe.expire(page_key, self.cache_config.TTL_WEAK)
            for resource_key, resource_mapping in resources.items():
                pipe.rpush(
                    page_key, resource_key
                )  # Push key name for resource into list

                if member_dtype == "mapping":
                    pipe.hset(resource_key, mapping=cache_repr(resource_mapping))
                else:
                    pipe.set(resource_key, orjson.dumps(cache_repr(resource_mapping)))
                pipe.expire(resource_key, self.cache_config.TTL_STRONG)

            pipe.rpush(page_key, cursor or "")
            await pipe.execute()

    async def update_cache_version(self, resource_name: str) -> None:
        await self.redis_client.hincrby(self.PAGINATION_VERSION_MAP, resource_name)

    @classmethod
    def _pipelined_update_cache_version(
        cls, resource_name: str, pipeline: Pipeline
    ) -> None:
        pipeline.hincrby(cls.PAGINATION_VERSION_MAP, resource_name, 1)
