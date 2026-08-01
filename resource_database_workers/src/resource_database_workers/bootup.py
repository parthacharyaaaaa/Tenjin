import asyncio
from functools import partial
from typing import Any, Callable, Coroutine, Final, Mapping

from resource_database_workers.datastructures.worker_inputs import (
    COUNTER_WORKER_INPUT,
    STATUS_PROXY_NAME,
    WORKER_INPUT_DATA_MAPPING,
    UpstreamDispatcherInput,
)
from resource_database_workers.datastructures.processors import (
    EVENT_WORKER_MAPPING,
)
from resource_database_workers.datastructures.streams import (
    STREAM_CONSUMER_MAPPING,
)
from resource_database_workers.config.worker_config import (
    CounterWorkersConfig,
    StreamWorkersConfig,
)
from resource_database_workers.datastructures.status_indicator import (
    StatusController,
    StatusProxy,
)
from resource_database_workers.dependencies import (
    get_queue_registry,
)
from resource_database_workers.config.config import AppConfig
from resource_database_workers.utils.strings import (
    generate_worker_name,
)
from resource_database_workers.utils.typing import (
    GenericDataclass,
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
    counter_config: CounterWorkersConfig, status_proxy: StatusProxy
) -> dict[str, Callable[[], Coroutine[None, None, None]]]:
    callable_mapping: dict[str, Callable[[], Coroutine[None, None, None]]] = {}

    prepared_inputs: Final[dict[str, Any]] = (
        COUNTER_WORKER_INPUT.__dataclass_fields__ | {STATUS_PROXY_NAME: status_proxy}
    )
    for i in range(1, counter_config.WORKER_COUNT + 1):
        callable_mapping[generate_worker_name("counter", i)] = partial(
            batch_update_counters, **prepared_inputs
        )
    for i in range(1, counter_config.RETRY_WORKER_COUNT + 1):
        callable_mapping[generate_worker_name("retry_counter", i)] = partial(
            batch_update_retry_counters, **prepared_inputs
        )
    return callable_mapping


def _stream_worker_wrapper(
    stream_config: StreamWorkersConfig, status_proxy: StatusProxy
) -> dict[str, Callable[[], Coroutine[None, None, None]]]:
    worker_mapping: dict[str, Callable[[], Coroutine[None, None, None]]] = {}

    reader_callable: Callable = STREAM_CONSUMER_MAPPING[stream_config.STREAM]
    for event, worker_count in stream_config.EVENT_WORKER_COUNT_MAPPING.items():
        worker_callable: Callable = EVENT_WORKER_MAPPING[event]
        worker_arguments: GenericDataclass = WORKER_INPUT_DATA_MAPPING[event]
        for i in range(1, worker_count + 1):
            worker_mapping[generate_worker_name(event, i)] = partial(
                worker_callable,
                **(
                    worker_arguments.__dataclass_fields__
                    | {STATUS_PROXY_NAME: status_proxy}
                ),
            )
    queue_mapping = get_queue_registry().resolve_stream_reader_queue_mapping(
        stream_config.STREAM
    )
    reader_task_input: UpstreamDispatcherInput = UpstreamDispatcherInput(
        stream_name=stream_config.STREAM, queue_mapping=queue_mapping
    )
    for i in range(1, stream_config.READER_COUNT + 1):
        worker_mapping[
            generate_worker_name(stream_config.STREAM, i, base_name="reader")
        ] = partial(
            reader_callable,
            **(
                reader_task_input.__dataclass_fields__
                | {STATUS_PROXY_NAME: status_proxy}
            ),
        )
    return worker_mapping


async def spawn_tasks(
    app_config: AppConfig,
    worker_config: CounterWorkersConfig | StreamWorkersConfig,
) -> None:
    status_controller: Final[StatusController] = StatusController()
    status_proxy: StatusProxy = StatusProxy(status_controller)
    if isinstance(worker_config, CounterWorkersConfig):
        await tasks_wrapper(
            _counter_worker_wrapper(worker_config, status_proxy),
            app_config.WORKER.GRACEFUL_SHUTDOWN_PERIOD,
            status_controller,
        )
    else:
        await tasks_wrapper(
            _stream_worker_wrapper(worker_config, status_proxy),
            app_config.WORKER.GRACEFUL_SHUTDOWN_PERIOD,
            status_controller,
        )
