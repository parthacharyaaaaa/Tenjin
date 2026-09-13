from redis.asyncio.client import Redis
from resource_database_workers.utils.strings import generate_checkpoint_name
from typing import Iterable, Callable, TypeVar

from redis.asyncio.client import Pipeline
from redis.typing import EncodableT

from resource_auxillary.cache import derive_cache_key
from resource_auxillary.datastructures.database import StrongEntity

T = TypeVar("T")


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


def set_downstream_checkpoint(
    pipeline: Pipeline,
    prefix: str,
    event_id: int,
    offset: EncodableT,
    *,
    lifespan: int | None = None,
) -> None:
    pipeline.set(generate_checkpoint_name(prefix, event_id), offset, ex=lifespan)


async def clear_downstream_checkpoint(
    redis: Redis,
    prefix: str,
    event_id: int,
) -> None:
    await redis.delete(generate_checkpoint_name(prefix, event_id))


async def fetch_event_processing_checkpoint(
    redis: Redis,
    prefix: str,
    event_id: int,
    *,
    cast_function: Callable[[str], T] = int,
    default_checkpoint: T = 0,
) -> T:
    res = await redis.get(generate_checkpoint_name(prefix, event_id))
    if not res:
        return default_checkpoint
    return cast_function(res)
