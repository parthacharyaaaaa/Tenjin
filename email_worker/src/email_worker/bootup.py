import asyncio
from functools import partial
from typing import Any, Callable, Coroutine, Final, Mapping, MutableMapping

from resource_auxillary.datastructures.status_indicator import (
    StatusController,
    StatusProxy,
)
from email_worker.config.worker_config import WorkerCountConfig
from email_worker.datastructures.queue_registry import QueueRegistry
from email_worker.dependencies import get_queue_registry, get_email_config
from email_worker.tasks.emailing import email_dispatcher
from email_worker.tasks.stream_reading import upstream_dispatcher
from email_worker.datastructures.worker_inputs import (
    GeneralEmailInput,
    UpstreamDispatcherInput,
)
from email_worker.datastructures.streams import STREAM_EVENT_MAPPING

type _t_worker_task_callable = Callable[[], Coroutine[None, None, None]]


async def tasks_wrapper(
    callable_details: Mapping[str, _t_worker_task_callable],
    graceful_shutdown_timeout: float,
    status_controller: StatusController,
) -> None:
    tasks: tuple[asyncio.Task[None], ...] = tuple(
        asyncio.create_task(worker_callable(), name=name)
        for name, worker_callable in callable_details.items()
    )
    failed, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    assert len(failed) == 1  # nosec

    # Save initial exception
    exception: Exception = next(iter(failed)).exception()  # type: ignore[reportAssignmentType]

    status_controller.status_ok = False
    done, pending = await asyncio.wait(pending, timeout=graceful_shutdown_timeout)

    for task in pending:
        task.cancel()

    forced_cancellation_results = await asyncio.gather(
        *(pending), return_exceptions=True
    )
    exception.add_note(
        "\n".join(
            (
                f"Forced cancelled {len(forced_cancellation_results)} tasks.",
                "Cancellation results:",
                ", ".join(str(i) for i in forced_cancellation_results),
            )
        )
    )
    raise exception


def _prepare_reader_task_callables(
    worker_count_config: WorkerCountConfig,
    status_proxy: StatusProxy,
    tasks_mapping: MutableMapping[str, _t_worker_task_callable],
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
    tasks_mapping: MutableMapping[str, _t_worker_task_callable],
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
    callable_details: dict[str, _t_worker_task_callable] = {}

    _prepare_reader_task_callables(worker_count_config, status_proxy, callable_details)
    _prepare_worker_task_callables(worker_count_config, status_proxy, callable_details)

    await tasks_wrapper(
        callable_details,
        get_email_config().WORKER.GRACEFUL_SHUTDOWN_PERIOD,
        status_controller,
    )
