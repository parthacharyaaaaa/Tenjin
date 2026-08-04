from typing import Mapping, Sequence

from redis.asyncio import Redis

from resource_auxillary.strings import StreamName
from resource_database_workers.datastructures.redis import (
    XInfoGroupResponse,
    XPendingRangeResponse,
)
from resource_database_workers.src.resource_database_workers.workers.redis.helpers import (
    get_min_max_from_xread,
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
    for pending_event in pending_event_data:
        if (
            pending_event["times_delivered"] < max_deliveries
            and pending_event["time_since_delivered"] > idle_time_threshold
        ):
            reclaimation_ids.append(pending_event["message_id"])
    if not reclaimation_ids:
        return

    await redis.xclaim(
        stream_name,
        group_name,
        reclaim_consumer_name,
        idle_time_threshold,
        reclaimation_ids,  # type: ignore
        justid=True,
    )


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
