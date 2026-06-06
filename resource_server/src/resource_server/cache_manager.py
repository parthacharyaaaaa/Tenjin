from dataclasses import dataclass
from typing import Any, ClassVar, Final, Literal, Mapping, Sequence

import orjson

from redis import RedisError
from redis.asyncio.client import Redis

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auxillary.typing_utils import SupportsAsyncRedis

from resource_server.config.sub_config import CacheConfig
from resource_server.utils.singleton import SingletonMetaclass
from resource_server.datastructures.exceptions import (
    ResourceNotFoundException,
    ResourceDeletedException,
    OperationUnderwayException,
)


@dataclass(init=False, slots=True, weakref_slot=True)
class CacheManager(metaclass=SingletonMetaclass):

    redis_client: SupportsAsyncRedis
    cache_config: CacheConfig

    allowed_intents: ClassVar[frozenset[str]] = frozenset(
        [
            CacheConfig.RESOURCE_DELETION_PENDING_FLAG,
            CacheConfig.RESOURCE_CREATION_PENDING_ALT_FLAG,
            CacheConfig.RESOURCE_CREATION_PENDING_FLAG,
        ]
    )

    def __init__(self, redis: Redis, cache_config: CacheConfig) -> None:
        self.redis_client = redis  # type: ignore
        self.cache_config = cache_config

    @classmethod
    def derive_intent_key(cls, flag: str, *args: str) -> str:
        if flag not in cls.allowed_intents:
            raise ValueError(
                " ".join(
                    ("Invalid intent flag, must be in:", ", ".join(cls.allowed_intents))
                )
            )
        return ":".join((flag, *args))

    @staticmethod
    def derive_lock_key(*args: str) -> str:
        return ":".join(("lock", *args))

    @staticmethod
    def derive_cache_key(resource_name: str, identifier: str | int) -> str:
        return ":".join((resource_name, str(identifier)))

    def derive_deletion_intent(self, resource_name: str, identifier: str | int) -> str:
        return ":".join(
            (
                self.cache_config.RESOURCE_DELETION_PENDING_FLAG,
                resource_name,
                str(identifier),
            )
        )

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

    async def update_global_counter(
        self,
        delta: int,
        database_session: AsyncSession,
        table: str,
        column: str,
        identifier: str,
        hashmap_key: str | None = None,
    ) -> None:
        """
        Update the global counter for a resource's field
        Args:
            self.redis_client: Redis instance connected to server holding the counters
            delta: Whether to increment or decrement the counter
            database_session: session fetch data from in case the counter is absent in Redis, and a new one needs to be made
            table: Table name for the entity to be updated
            column: Column of entity associated with the counter
            identifier: Unique ID to identify the target record
            hashmap_key: Optional key name for hashmap. If not passed, constructed as table:column
        """
        hashmap_key = hashmap_key or f"{table}:{column}"
        counter = await self.redis_client.hget(hashmap_key, identifier)
        if counter:
            await self.redis_client.hincrby(hashmap_key, identifier, delta)
            return

        # No counter, create one
        currentCount: int = (
            await database_session.execute(
                text(f"SELECT {column} FROM {table} WHERE id = :identifier"),
                {"identifier": identifier},
            )
        ).scalar_one()

        op = await self.redis_client.hsetnx(
            hashmap_key, identifier, currentCount + delta
        )
        if not op:
            # Counter made by another worker, update in place
            await self.redis_client.hincrby(hashmap_key, identifier, delta)

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

    async def posts_cache_precheck(
        self,
        post_id: str,
        post_cache_key: str,
        post_deletion_intent_flag: str,
        action_flag: str,
        lock_name: str,
        conflicting_intent: str | None = None,
    ) -> tuple[dict | None, str | None]:
        """
        Consult cache and perform a check on a given post to try to
        validate the request thriugh cache and minimize DB lookups.
        Although this cannot guarantee post validity,
        if a post is found to be invalid through cache,
        an appropriate HTTP exception is raised.
        If all checks pass, then the post_mapping (if found)
        and the latest intent (if found) are returned
        Args:
            post_id: Unique identifier for post
            post_cache_key: cache key for this post
            post_deletion_intent_flag: Deletion flag name for this post
            action_flag: Flag name to check for latest user intent for this post
            lock_name: Name of lock for this action
            conflicting_intent: If specified, latest user intent is checked against this value for early rejection

        Returns:
            post_mapping (dict), latest_intent (str)
        """
        async with self.redis_client.pipeline() as pipe:
            # Post
            pipe.hgetall(post_cache_key)
            pipe.get(
                post_deletion_intent_flag
            )  # Existence alone is enough to prove post deletion

            pipe.get(action_flag)  # Get latest intent (if any) for this action
            pipe.get(lock_name)

            post_mapping, post_deletion_intent, latest_intent, lock = (
                await pipe.execute()
            )

        if post_deletion_intent:  # Deletion written in cache
            raise ResourceDeletedException(
                "This post has been permanently deleted, and will soon be unavailable"
            )
        if (
            post_mapping and self.cache_config.NF_SENTINEL_KEY in post_mapping
        ):  # Post non-existence written in cache
            await self.hset_with_ttl(
                post_cache_key, post_mapping, self.cache_config.TTL_EPHEMERAL
            )  # Reannounce post non-existence
            raise ResourceNotFoundException(
                f"No post with ID {post_id} could be found (Never existed, or deleted)"
            )
        if lock:  # Stop race condition early
            raise OperationUnderwayException(
                "Another worker is processing this exact request at the moment"
            )
        if (latest_intent and not conflicting_intent) or (
            conflicting_intent and latest_intent == conflicting_intent
        ):  # Stop duplicate requests
            raise OperationUnderwayException(
                f"This action for post {post_id} has already been requested"
            )

        return (
            post_mapping,
            latest_intent,
        )  # post_mapping and latest intent may be required by the endpoint later

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

    async def admin_cache_precheck(
        self,
        user_id: int | str,
        user_cache_key: str,
        forum_id: int | str,
        forum_cache_key: str,
        admin_flag: str,
        *,
        lock_name: str | None = None,
        user_status_flag: str | None = None,
        forum_deletion_flag: str | None = None,
        conflicting_intents: Sequence[str] | None = None,
        message: str | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
        user_status_flag = user_status_flag or f"alive_status:{user_id}"
        forum_deletion_flag = forum_deletion_flag or f"delete:{forum_id}"
        lock_name = lock_name or f"lock:{admin_flag}"
        async with self.redis_client.pipeline(transaction=False) as pipe:
            # User
            pipe.get(user_status_flag)
            pipe.hgetall(user_cache_key)

            # Forum
            pipe.get(forum_deletion_flag)
            pipe.hgetall(forum_cache_key)

            # Forum admin status
            pipe.get(admin_flag)

            # Race
            pipe.get(lock_name)
            (
                user_status,
                user_mapping,
                forum_mapping,
                forum_deletion_intent,
                latest_intent,
                lock,
            ) = await pipe.execute()

        # Forum checks
        if forum_deletion_intent:
            raise ResourceDeletedException(
                f"Forum with id {forum_id} has just been deleted"
            )
        if forum_mapping and self.cache_config.NF_SENTINEL_KEY in forum_mapping:
            await self.hset_with_ttl(
                forum_cache_key,
                {
                    self.cache_config.NF_SENTINEL_KEY: self.cache_config.NF_SENTINEL_VALUE
                },
                self.cache_config.TTL_EPHEMERAL,
            )  # Reannounce non-existence of this forum
            raise ResourceNotFoundException(f"No forum with ID {forum_id} found")

        # User checks
        if user_status == self.cache_config.RESOURCE_DELETION_PENDING_FLAG:
            raise ResourceDeletedException(
                f"User with id {user_id} has just been deleted"
            )
        if (
            user_mapping and self.cache_config.NF_SENTINEL_KEY in user_mapping
        ):  # User (to be made an admin) doesn't exist or has been deleted
            await self.hset_with_ttl(
                user_cache_key,
                {
                    self.cache_config.NF_SENTINEL_KEY: self.cache_config.NF_SENTINEL_VALUE
                },
                self.cache_config.TTL_EPHEMERAL,
            )  # Reannounce non-existence of this forum
            raise ResourceNotFoundException(f"No user with ID {user_id} found")

        if (conflicting_intents and latest_intent in conflicting_intents) or (
            not conflicting_intents and latest_intent
        ):
            raise OperationUnderwayException(
                message
                or f"This action is currently invalid, as the request may be logically invalid or duplicate"
            )
        if lock:
            raise OperationUnderwayException(
                "A request for this action is already enqueued"
            )

        return forum_mapping, user_mapping, latest_intent

    async def fetch_from_cache(
        self,
        cache_key: str,
        *,
        ttl_cap: int | None = None,
        ttl_promotion: int | None = None,
        ttl_ephemeral: int | None = None,
        dtype: Literal["mapping", "string"] = "mapping",
        suppress_errors: bool = True,
    ) -> dict | None:
        """
        Consult Redis cache and attempt to fetch the given key.
        Args:
            cache_key: Name of the key to search for
            ttl_cap: Maximum TTL in seconds for any entry in cache
            ttl_promotion: Seconds to add to an existing entry's TTL on cache hit
            ttl_ephemeral: TTL in seconds for ephemeral announcements
            dtype: Redis data structure assosciated with this item. Defaults to hashmap
            nf_repr: Representation of a key that does not exist. If dtype is mapping, then it is interpreted as {nf_val:-1}
            suppress_errors: Flag to allow silent failures. Ideally this should be set to True to allow graceful fallback to database

        Returns
            {self.cache_config.NF_SENTINEL_KEY : True} if nf_repr found, None on cache miss/suppressed failure, and cached mapping on cache hits
        """
        try:
            async with self.redis_client.pipeline(transaction=False) as pipe:
                if dtype == "mapping":
                    pipe.hgetall(cache_key)
                else:
                    pipe.get(cache_key)
                pipe.ttl(cache_key)
                cache_entry, ttl = await pipe.execute()

            if not cache_entry:  # Cache miss
                return None

            ttl_ephemeral = ttl_ephemeral or self.cache_config.TTL_EPHEMERAL
            ttl_promotion = ttl_promotion or self.cache_config.TTL_PROMOTION
            ttl_cap = ttl_cap or self.cache_config.TTL_CAP

            if dtype == "mapping" and self.cache_config.NF_SENTINEL_KEY in cache_entry:
                async with self.redis_client.pipeline() as pipe:
                    pipe.hset(cache_key, mapping=self.cache_config.NF_MAPPING)
                    pipe.expire(cache_key, ttl_ephemeral)
                    await pipe.execute()
                return self.cache_config.NF_MAPPING

            elif dtype == "string" and cache_entry == self.cache_config.NF_SENTINEL_KEY:
                await self.redis_client.set(
                    cache_key, self.cache_config.NF_SENTINEL_KEY, ttl_ephemeral
                )
                return self.cache_config.NF_MAPPING

            # Cache hit, and resource actually exists
            await self.redis_client.expire(cache_key, min(ttl_cap, ttl_promotion + ttl))
            return cache_entry if dtype == "mapping" else orjson.loads(cache_entry)

        except RedisError as e:
            if suppress_errors:
                return None
            else:
                raise RuntimeError("Unsuppressed cache failure") from e

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

    async def set_intent(self, intent: str, *intent_args: str, ttl: int | None = None):
        if intent not in self.allowed_intents:
            raise ValueError(
                " ".join(
                    (
                        "Intent not allowed, must be one of:",
                        ", ".join(self.allowed_intents),
                    )
                )
            )
        ttl = ttl or self.cache_config.TTL_STRONGEST
        await self.redis_client.set(
            self.derive_intent_key(intent, *intent_args), 1, ex=ttl
        )
