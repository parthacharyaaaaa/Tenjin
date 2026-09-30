import asyncio
import traceback
from collections.abc import Callable, Coroutine, Mapping
from datetime import timedelta
from typing import Any

from resource_auxillary.datastructures.status_indicator import StatusController

type t_worker_task_callable = Callable[[], Coroutine[None, None, Any]]


async def tasks_bootup_wrapper(
    worker_callables: Mapping[str, t_worker_task_callable],
    graceful_shutdown_timeout: timedelta,
    status_controller: StatusController,
) -> None:
    tasks: tuple[asyncio.Task[None], ...] = tuple(
        asyncio.create_task(worker_callable(), name=name)
        for name, worker_callable in worker_callables.items()
    )
    failed, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

    status_controller.status_ok = False
    _done, pending = await asyncio.wait(
        pending, timeout=graceful_shutdown_timeout.total_seconds()
    )

    for task in pending:
        task.cancel()

    forced_cancellation_results = await asyncio.gather(
        *(pending), return_exceptions=True
    )

    failed_tasks_info_string = "\n\n".join(
        (
            f"Task: {task.get_name()}\n"
            f"Exception: {type(exception).__name__}: {exception}\n"
            "Traceback:\n"
            f"{''.join(traceback.format_exception(exception))}"
        )
        for task in failed
        if not task.cancelled() and (exception := task.exception()) is not None
    )
    forced_cancellation_info_string = "\n\n".join(
        (f"Task: {task.get_name()}\nResult: {type(result).__name__}: {result}")
        for task, result in zip(pending, forced_cancellation_results)
    )

    exception = Exception("Worker tasks terminated unexpectedly")
    exception.add_note(
        f"Failed tasks ({len(failed)}):\n{failed_tasks_info_string or '<none>'}"
    )
    exception.add_note(
        f"Forced-cancelled tasks ({len(pending)}):\n"
        f"{forced_cancellation_info_string or '<none>'}"
    )

    raise exception
