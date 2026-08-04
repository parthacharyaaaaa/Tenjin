"""Core worker utilities for stream-bound event processing"""

from typing import Sequence, Iterable

import orjson
from redis.asyncio import Redis
from redis.asyncio.client import Pipeline

from auxillary.utils import cache_repr

from resource_auxillary.cache import NF_MAPPING
from resource_auxillary.strings import StreamName
from resource_auxillary.events import (
    CacheUpdate,
    CounterUpdate,
    IntentUpdate,
    StreamedEvent,
    Event,
)
from resource_database_workers.utils.lua_commands import (
    CONDITIIONAL_DELETE_TARGET_INTENT_TEMPLATE,
)


async def amortize_event(
    redis: Redis,
    events: Sequence[StreamedEvent],
    event_stream_name: StreamName,
    group_name: str,
    dlq_stream_name: StreamName,
) -> None:
    async with redis.pipeline(transaction=True) as pipeline:
        for event in events:
            pipeline.xack(event_stream_name, group_name, event.event_id)
            pipeline.xadd(dlq_stream_name, cache_repr(event), id=event.event_id)
        await pipeline.execute()


async def acknowledge_event(
    redis: Redis,
    events: Sequence[StreamedEvent],
    event_stream_name: StreamName,
    group_name: str,
) -> None:
    async with redis.pipeline(transaction=True) as pipeline:
        for event in events:
            pipeline.xack(event_stream_name, group_name, event.event_id)
        await pipeline.execute()


async def stream_events(
    redis: Redis, events: Iterable[Event], stream_name: StreamName
) -> None:
    async with redis.pipeline(transaction=True) as pipeline:
        for event in events:
            await pipeline.xadd(stream_name, cache_repr(event))
        await pipeline.execute()


def _emit_intent_invalidations(
    pipeline: Pipeline, intent_updates: Iterable[Sequence[IntentUpdate]]
) -> None:
    for event_intent_updates in intent_updates:
        for resource_intent_update in event_intent_updates:
            pipeline.eval(
                CONDITIIONAL_DELETE_TARGET_INTENT_TEMPLATE,
                1,
                resource_intent_update.intent_name,
                resource_intent_update.intent_value,
            )


def _emit_cache_invalidation_side_effects(
    pipeline: Pipeline, cache_side_effects: Iterable[Sequence[CacheUpdate]]
) -> None:
    for event_cache_invalidations in cache_side_effects:
        for resource_cache_invalidation in event_cache_invalidations:
            if resource_cache_invalidation.operation == "invalidate":
                pipeline.delete(resource_cache_invalidation.cache_key)
                continue
            if resource_cache_invalidation.resource_type == "mapping":
                pipeline.hset(resource_cache_invalidation.cache_key, mapping=NF_MAPPING)
                # TODO: Remove magic numbers for expiry values
                pipeline.expire(resource_cache_invalidation.cache_key, 60)
            else:
                pipeline.set(
                    resource_cache_invalidation.cache_key,
                    orjson.dumps(NF_MAPPING),
                    ex=60,
                )


def _emit_counter_side_effects(
    pipeline: Pipeline, counter_side_effects: Iterable[Sequence[CounterUpdate]]
) -> None:
    for event_side_effects in counter_side_effects:
        for side_effect in event_side_effects:
            pipeline.hincrby(
                side_effect.counter_group, side_effect.cache_key, side_effect.delta
            )


async def atomic_ack_and_emit_side_effects(
    redis: Redis,
    events: Sequence[StreamedEvent],
    stream_name: StreamName,
    group_name: str,
) -> None:
    async with redis.pipeline(transaction=True) as pipeline:
        # Acknowledge
        for event in events:
            pipeline.xack(stream_name, group_name, event.event_id)

        # Emit side effects
        _emit_intent_invalidations(
            pipeline, (i.side_effects.intent_updates for i in events)
        )
        _emit_counter_side_effects(
            pipeline, (i.side_effects.counter_updates for i in events)
        )
        _emit_cache_invalidation_side_effects(
            pipeline, (i.side_effects.cache_invalidations for i in events)
        )
        await pipeline.execute()
