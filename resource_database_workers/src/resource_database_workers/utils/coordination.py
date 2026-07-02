from contextlib import asynccontextmanager
from datetime import datetime
from typing import Iterable, Sequence
from uuid import uuid4

import orjson
from psycopg import AsyncConnection, sql
from psycopg.errors import IntegrityError

from redis.asyncio import Redis
from redis.asyncio.client import Pipeline

from resource_auxillary.datastructures.database import EventLiteral
from resource_auxillary.cache import NF_MAPPING
from resource_auxillary.events import (
    CacheUpdate,
    IntentUpdate,
    StreamedEvent,
    CounterUpdate,
)

from resource_database_workers.utils.lua_commands import (
    CONDITIIONAL_DELETE_TARGET_INTENT_TEMPLATE,
)
from resource_database_workers.utils.sql_templates import (
    prepare_batch_dedup_sql,
    prepare_single_dedup_sql,
    prepare_temp_table_sql,
    prepare_weak_insertion_copy_sql,
)


@asynccontextmanager
async def locked_operation(redis: Redis, lock_name: str):
    try:
        yield
    finally:
        await redis.delete(lock_name)


async def dedup_insert_event(
    conn: AsyncConnection, event_id: int, acknowledgement_time: datetime | None = None
) -> bool:
    dedup_insertion_statement: sql.Composed = prepare_single_dedup_sql(
        event_id, acknowledgement_time
    )
    try:
        async with conn.transaction():
            await conn.execute(dedup_insertion_statement)
        return True
    except IntegrityError:
        await conn.rollback()
        return False


async def batch_dedup_insert_events(
    conn: AsyncConnection,
    event_ids: Iterable[int],
    acknowledgement_time: datetime | None = None,
) -> tuple[int, ...]:
    acknowledgement_time = acknowledgement_time or datetime.now()
    temp_table_name: str = f"_temp_{uuid4().hex}_{acknowledgement_time.isoformat()}"

    await conn.execute(
        prepare_temp_table_sql(temp_table_name, EventLiteral.EVENTS_TABLE_NAME)
    )
    async with conn.cursor() as cursor:
        async with cursor.copy(
            prepare_weak_insertion_copy_sql(
                temp_table_name,
                EventLiteral.EVENT_ID_COLUMN_NAME,
                EventLiteral.EVENT_TIMESTAMP_COLUMN_NAME,
            )
        ) as copy:
            for event_id in event_ids:
                await copy.write_row((event_id, acknowledgement_time))
        await cursor.execute(prepare_batch_dedup_sql(temp_table_name))
        return tuple(i[0] for i in await cursor.fetchall())


async def atomic_emit_side_effects(
    redis: Redis, events: Sequence[StreamedEvent]
) -> None:
    async with redis.pipeline(transaction=True) as pipeline:
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
