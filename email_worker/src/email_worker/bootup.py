import asyncio
from functools import partial
from typing import Mapping

from resource_auxillary.datastructures.status_indicator import StatusController, StatusProxy

from email_worker.dependencies import get_email_config, get_redis_client
from email_worker.tasks import email_dispatcher

def _prepare_email_dispatcher_task() -> partial:
    return partial(
        email_dispatcher,
        get_email_config(),
        get_redis_client(),
        
    )

async def tasks_wrapper(
    worker_count: int,
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

async def spawn_tasks(task_count: int) -> None:
