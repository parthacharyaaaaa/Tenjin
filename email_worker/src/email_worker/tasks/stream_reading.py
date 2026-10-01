from resource_auxillary.event_processing.queues.dispatch import base_dispatcher

from email_worker.dependencies.annotations import (
    CONSUMER_ID,
    DEAD_LETTER_STREAM_NAME,
    EMAIL_CONFIG,
    EVENT_STREAM_MANAGER,
    GROUP_NAME,
    QUEUE_REGISTRY,
    STREAM_NAME,
)


async def batch_dispatcher(
    config: EMAIL_CONFIG,
    event_stream_manager: EVENT_STREAM_MANAGER,
    queue_registry: QUEUE_REGISTRY,
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
