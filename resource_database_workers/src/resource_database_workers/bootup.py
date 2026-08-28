from resource_database_workers.config.sub_config import WorkerConfig
from resource_database_workers.dependencies.dependency_resolver import (
    inject_worker_dependencies,
)
from resource_database_workers.dependencies.dependency_annotations import GROUP_NAME
from resource_database_workers.dependencies.dependency_annotations import STREAM_NAME
from resource_database_workers.dependencies.dependency_annotations import STATUS_PROXY
from resource_database_workers.dependencies.dependency_resolver import (
    inject_stream_worker_dependencies,
)
from resource_database_workers.dependencies.event_dependencies import (
    EVENT_WORKER_DATA_MAPPING,
)
import asyncio
from typing import Any, Callable, Coroutine, Final, Mapping

from resource_database_workers.datastructures.streams import (
    STREAM_CONSUMER_MAPPING,
)
from resource_database_workers.config.worker_config import (
    CounterWorkersConfig,
    StreamWorkersConfig,
)
from resource_auxillary.datastructures.status_indicator import (
    StatusController,
    StatusProxy,
)
from resource_database_workers.config.config import AppConfig
from resource_database_workers.utils.strings import (
    generate_worker_name,
)
from resource_database_workers.tasks.counters import (
    batch_update_counters,
)
from resource_database_workers.tasks.counters import (
    batch_update_counters,
    batch_update_retry_counters,
)


async def tasks_wrapper(
    worker_callables: Mapping[str, Callable[[], Coroutine[None, None, None]]],
    graceful_shutdown_timeout: float,
    status_controller: StatusController,
) -> None:
    tasks: tuple[asyncio.Task[None], ...] = tuple(
        asyncio.create_task(worker_callable(), name=name)
        for name, worker_callable in worker_callables.items()
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


def _counter_worker_wrapper(
    worker_config: WorkerConfig,
    counter_config: CounterWorkersConfig,
    status_proxy: StatusProxy,
) -> dict[str, Callable[[], Any]]:
    worker_mapping: dict[str, Callable[[], Any]] = {}
    base_context: dict[Any, Any] = {
        STATUS_PROXY: status_proxy,
    }
    for i in range(1, counter_config.WORKER_COUNT + 1):
        worker_mapping[
            generate_worker_name(worker_config.COUNTER_WORKER_TASK_PREFIX, i)
        ] = inject_worker_dependencies(batch_update_counters, base_context)
    for i in range(1, counter_config.RETRY_WORKER_COUNT + 1):
        worker_mapping[
            generate_worker_name(worker_config.RETRY_COUNTER_WORKER_TASK_PREFIX, i)
        ] = inject_worker_dependencies(batch_update_retry_counters, base_context)
    return worker_mapping


def _stream_worker_wrapper(
    worker_config: WorkerConfig,
    stream_config: StreamWorkersConfig,
    status_proxy: StatusProxy,
    group_name: str,
) -> dict[str, Callable[[], Any]]:
    worker_mapping: dict[str, Callable[[], Any]] = {}
    base_context: dict[Any, Any] = {
        STATUS_PROXY: status_proxy,
        STREAM_NAME: stream_config.STREAM,
        GROUP_NAME: group_name,
    }
    # event worker initialization
    for event, worker_count in stream_config.EVENT_WORKER_COUNT_MAPPING.items():
        worker_callable, worker_context = EVENT_WORKER_DATA_MAPPING[event]
        for i in range(1, worker_count + 1):
            worker_mapping[
                generate_worker_name(worker_config.STREAM_WORKER_TASK_PREFIX, i)
            ] = inject_stream_worker_dependencies(event, worker_context | base_context)

    # stream reader initialization
    reader_callable: Callable[..., Any] = STREAM_CONSUMER_MAPPING[stream_config.STREAM]
    for i in range(1, stream_config.READER_COUNT + 1):
        worker_mapping[
            generate_worker_name(
                stream_config.STREAM,
                i,
                base_name=worker_config.STREAM_READER_TASK_PREFIX,
            )
        ] = inject_worker_dependencies(reader_callable, base_context)

    return worker_mapping


async def spawn_tasks(
    app_config: AppConfig,
    worker_config: CounterWorkersConfig | StreamWorkersConfig,
) -> None:
    status_controller: Final[StatusController] = StatusController()
    status_proxy: StatusProxy = StatusProxy(status_controller)
    if isinstance(worker_config, CounterWorkersConfig):
        await tasks_wrapper(
            _counter_worker_wrapper(app_config.WORKER, worker_config, status_proxy),
            app_config.WORKER.GRACEFUL_SHUTDOWN_PERIOD,
            status_controller,
        )
    else:
        await tasks_wrapper(
            _stream_worker_wrapper(
                app_config.WORKER,
                worker_config,
                status_proxy,
                app_config.WORKER.CONSUMER_GROUP_NAME,
            ),
            app_config.WORKER.GRACEFUL_SHUTDOWN_PERIOD,
            status_controller,
        )
