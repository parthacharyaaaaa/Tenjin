from functools import partial
from typing import Any, Final, MutableMapping

from resource_auxillary.boot_utils import t_worker_task_callable, tasks_bootup_wrapper
from resource_auxillary.datastructures.status_indicator import (
    StatusController,
    StatusProxy,
)

from email_worker.config.sub_config import WorkerCountConfig
from email_worker.datastructures.queue_registry import QueueRegistry
from email_worker.datastructures.streams import STREAM_EVENT_MAPPING
from email_worker.datastructures.worker_inputs import (
    GeneralEmailInput,
    UpstreamDispatcherInput,
)
from email_worker.dependencies.injections import get_email_config, get_queue_registry
from email_worker.tasks.emailing import email_dispatcher
from email_worker.tasks.stream_reading import upstream_dispatcher


def _prepare_reader_task_callables(
    worker_count_config: WorkerCountConfig,
    status_proxy: StatusProxy,
    tasks_mapping: MutableMapping[str, t_worker_task_callable],
) -> None:
    input_fields: Final[dict[str, Any]] = UpstreamDispatcherInput(
        status_proxy=status_proxy
    ).__dataclass_fields__
    for i in range(1, worker_count_config.READER_COUNT + 1):
        tasks_mapping[f"reader_{i}"] = partial(
            upstream_dispatcher, **input_fields.copy()
        )


def _prepare_worker_task_callables(
    worker_count_config: WorkerCountConfig,
    status_proxy: StatusProxy,
    tasks_mapping: MutableMapping[str, t_worker_task_callable],
) -> None:
    queue_registry: Final[QueueRegistry] = get_queue_registry()
    for event, worker_count in worker_count_config.EVENT_WORKER_COUNT_MAPPING.items():
        for i in range(1, worker_count + 1):
            tasks_mapping[f"dispatcher_{i}"] = partial(
                email_dispatcher,
                **GeneralEmailInput(
                    stream_name=STREAM_EVENT_MAPPING[event],
                    events_queue=queue_registry.event_queue_mapping[event],
                    status_proxy=status_proxy,
                ).__dataclass_fields__,
            )


async def spawn_tasks(worker_count_config: WorkerCountConfig) -> None:
    status_controller: Final[StatusController] = StatusController()
    status_proxy: Final[StatusProxy] = StatusProxy(status_controller)
    callable_details: dict[str, t_worker_task_callable] = {}

    _prepare_reader_task_callables(worker_count_config, status_proxy, callable_details)
    _prepare_worker_task_callables(worker_count_config, status_proxy, callable_details)

    await tasks_bootup_wrapper(
        callable_details,
        get_email_config().WORKER.GRACEFUL_SHUTDOWN_PERIOD,
        status_controller,
    )
