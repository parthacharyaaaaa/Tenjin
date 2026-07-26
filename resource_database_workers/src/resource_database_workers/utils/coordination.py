import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
import random
from typing import Iterable, Mapping, Sequence
from uuid import uuid4

from psycopg import AsyncConnection, sql
from psycopg.errors import IntegrityError

from redis.asyncio import Redis

from resource_auxillary.datastructures.database import EventLiteral

from resource_auxillary.strings import StreamName
from resource_database_workers.datastructures.redis import (
    XInfoGroupResponse,
    XPendingRangeResponse,
)
from resource_database_workers.src.resource_database_workers.utils.worker_redis import (
    get_min_max_from_xread,
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


def calculate_exponential_backoff_time(
    cap: float, base: float, attempt: int, *, exponential: int = 2
) -> float:
    return min(cap, base * exponential**attempt)


async def exponential_jittered_backoff(
    cap: float, base: float, attempt: int, *, exponential: int = 2
) -> None:
    await asyncio.sleep(
        random.uniform(
            0,
            calculate_exponential_backoff_time(
                cap, base, attempt, exponential=exponential
            ),
        )  # nosec
    )


async def get_existing_worker_groups(
    redis: Redis, stream_names: Sequence[StreamName], consumer_group_name: str
) -> set[StreamName]:
    async with redis.pipeline(transaction=True) as pipeline:
        for stream_name in stream_names:
            pipeline.xinfo_groups(stream_name)
        results: list[list[XInfoGroupResponse]] = await pipeline.execute()

    return set(
        stream_names[i]
        for i, result in enumerate(results)
        if result and any(g_info["name"] == consumer_group_name for g_info in result)
    )


async def establish_consumer_groups(
    redis: Redis,
    stream_consumer_mapping: Mapping[StreamName, Sequence[str]],
    consumer_group_name: str,
) -> None:
    existing_group: set[StreamName] = await get_existing_worker_groups(
        redis, list(stream_consumer_mapping.keys()), consumer_group_name
    )

    async with redis.pipeline(transaction=True) as pipeline:
        for stream_name, consumer_names in stream_consumer_mapping.items():
            if stream_name not in existing_group:
                # Consumer group does not exist
                pipeline.xgroup_create(stream_name, consumer_group_name, mkstream=True)
            for consumer_name in consumer_names:
                pipeline.xgroup_createconsumer(
                    stream_name, consumer_name, consumer_group_name
                )
        await pipeline.execute()


async def reclaim_pending_events(
    redis: Redis,
    stream_name: StreamName,
    group_name: str,
    read_count: int,
    reclaim_consumer_name: str,
    idle_time_threshold: int,
    max_deliveries: int,
) -> None:
    pending_event_data: list[XPendingRangeResponse] = await redis.xpending_range(
        stream_name, group_name, "-", "+", read_count
    )
    if not pending_event_data:
        return
    reclaimation_ids: list[str] = []
    async with redis.pipeline(transaction=True) as pipeline:
        for pending_event in pending_event_data:
            if (
                pending_event["times_delivered"] < max_deliveries
                and pending_event["time_since_delivered"] > idle_time_threshold
            ):
                reclaimation_ids.append(pending_event["message_id"])

        pipeline.xclaim(
            stream_name,
            group_name,
            reclaim_consumer_name,
            idle_time_threshold,
            reclaimation_ids,  # type: ignore
            justid=True,
        )
    await pipeline.execute()


async def reclaim_dead_events(
    redis: Redis,
    stream_name: StreamName,
    dlq_stream_name: StreamName,
    group_name: str,
    read_count: int,
    max_deliveries: int,
) -> None:
    pending_event_data: list[XPendingRangeResponse] = await redis.xpending_range(
        stream_name, group_name, "-", "+", read_count
    )
    if not pending_event_data:
        return
    dlq_reclamation_ids: set[str] = set()
    for pending_event in pending_event_data:
        if pending_event["times_delivered"] >= max_deliveries:
            dlq_reclamation_ids.add(pending_event["message_id"])
    if not dlq_reclamation_ids:
        return

    min_val, max_val = get_min_max_from_xread(dlq_reclamation_ids)
    events = [
        event
        for event in await redis.xrange(stream_name, min_val, max_val)
        if event[0] in dlq_reclamation_ids
    ]
    async with redis.pipeline(transaction=True) as pipeline:
        for event in events:
            pipeline.xadd(dlq_stream_name, event[1])
        pipeline.xackdel(stream_name, group_name, *dlq_reclamation_ids)
        await pipeline.execute()
