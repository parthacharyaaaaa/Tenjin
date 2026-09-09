from typing import Iterable

from redis.asyncio.client import Pipeline

from resource_auxillary.cache import derive_cache_key
from resource_auxillary.datastructures.database import StrongEntity


def register_counter_decrement_updates(
    pipeline: Pipeline,
    deltas: Iterable[tuple[str, int]],
    hashmap_name: str,
    hash_key_prefix: StrongEntity,
) -> None:
    for delta in deltas:
        pipeline.hincrby(
            hashmap_name,
            derive_cache_key(hash_key_prefix, delta[0]),
            -delta[1],
        )
