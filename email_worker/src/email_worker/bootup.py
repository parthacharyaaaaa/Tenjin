from collections.abc import Callable
from typing import Any, Final

from auxillary.dependencies.resolution import inject_worker_dependencies
from resource_auxillary.boot_utils import t_worker_task_callable, tasks_bootup_wrapper
from resource_auxillary.datastructures.status_indicator import (
    StatusController,
    StatusProxy,
)
from resource_auxillary.event_processing.queues.registry import EventQueueRegistry
from resource_auxillary.strings import EventName

from email_worker.config.sub_config import EmailWorkerConfig
from email_worker.config.worker_config import StreamWorkersConfig
from email_worker.datastructures.streams import (
    STREAM_CONSUMER_MAPPING,
)
from email_worker.dependencies.annotations import (
    BATCHED_EVENT_QUEUE,
    GROUP_NAME,
    STATUS_PROXY,
    STREAM_NAME,
)
from email_worker.dependencies.injections import (
    get_app_config,
    get_email_config,
    get_queue_registry,
)
from email_worker.strings import generate_worker_name
from email_worker.tasks.emailing import email_dispatcher


def _stream_worker_wrapper(
    worker_config: EmailWorkerConfig,
    stream_config: StreamWorkersConfig,
    status_proxy: StatusProxy,
) -> dict[str, Callable[[], Any]]:
    worker_mapping: dict[str, Callable[[], Any]] = {}
    base_context: dict[Any, Any] = {
        STATUS_PROXY: status_proxy,
        GROUP_NAME: worker_config.CONSUMER_GROUP_NAME,
    }
    event_queue_registry: Final[EventQueueRegistry] = get_queue_registry()
    event_queue_annotation = BATCHED_EVENT_QUEUE  # For worker DI

    for stream, reader_count in stream_config.STREAM_READER_COUNT_MAPPING.items():
        # stream reader initialization
        reader_callable: Callable[..., Any] = STREAM_CONSUMER_MAPPING[stream]
        reader_context: dict[Any, Any] = {STREAM_NAME: stream}
        for i in range(1, reader_count + 1):
            worker_mapping[
                generate_worker_name(
                    stream,
                    i,
                    base_name=worker_config.WORKER_TASK_PREFIX,
                )
            ] = inject_worker_dependencies(
                reader_callable, base_context | reader_context
            )

        stream_worker_data: dict[EventName, int] = (
            stream_config.EVENT_WORKER_COUNT_MAPPING[stream]
        )
        for event, worker_count in stream_worker_data.items():
            event_queue_registry.register_event_queue(event, exist_ok=True)
            worker_context = {
                event_queue_annotation: event_queue_registry.get_event_queue(event),
                STREAM_NAME: stream,
            }
            for i in range(1, worker_count + 1):
                worker_mapping[
                    generate_worker_name(worker_config.WORKER_TASK_PREFIX, i)
                ] = inject_worker_dependencies(
                    email_dispatcher, worker_context | base_context
                )

    return worker_mapping


async def spawn_tasks(stream_worker_config: StreamWorkersConfig) -> None:
    status_controller: Final[StatusController] = StatusController()
    status_proxy: Final[StatusProxy] = StatusProxy(status_controller)
    email_worker_config: Final[EmailWorkerConfig] = get_app_config().EMAIL.WORKER
    callable_details: Final[dict[str, t_worker_task_callable]] = {}

    _stream_worker_wrapper(email_worker_config, stream_worker_config, status_proxy)

    await tasks_bootup_wrapper(
        callable_details,
        get_email_config().WORKER.GRACEFUL_SHUTDOWN_PERIOD,
        status_controller,
    )
