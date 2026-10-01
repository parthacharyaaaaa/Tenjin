from resource_auxillary.event_processing.queues.dispatch import base_dispatcher

from resource_database_workers.dependencies.annotations import (
    APP_CONFIG,
    CONSUMER_ID,
    DEAD_LETTER_QUEUE_REGISTRY,
    DEAD_LETTER_STREAM_NAME,
    EVENT_STREAM_MANAGER,
    GROUP_NAME,
    STREAM_NAME,
    UPSTREAM_QUEUE_REGISTRY,
)


async def upstream_dispatcher(
    config: APP_CONFIG,
    event_stream_manager: EVENT_STREAM_MANAGER,
    queue_registry: UPSTREAM_QUEUE_REGISTRY,
    dlq_stream_name: DEAD_LETTER_STREAM_NAME,
    stream_name: STREAM_NAME,
    group_name: GROUP_NAME,
    consumer_name: CONSUMER_ID,
    read_history: bool = True,
) -> None:
    await base_dispatcher(
        config.WORKER,
        event_stream_manager,
        queue_registry,
        dlq_stream_name,
        stream_name,
        group_name,
        consumer_name,
        read_history,
    )


async def dlq_dispatcher(
    config: APP_CONFIG,
    event_stream_manager: EVENT_STREAM_MANAGER,
    queue_registry: DEAD_LETTER_QUEUE_REGISTRY,
    dlq_stream_name: DEAD_LETTER_STREAM_NAME,
    stream_name: STREAM_NAME,
    group_name: GROUP_NAME,
    consumer_name: CONSUMER_ID,
    read_history: bool = True,
) -> None:
    await base_dispatcher(
        config.WORKER,
        event_stream_manager,
        queue_registry,
        dlq_stream_name,
        stream_name,
        group_name,
        consumer_name,
        read_history,
    )
