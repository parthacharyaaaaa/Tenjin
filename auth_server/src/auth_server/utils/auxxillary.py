import asyncio
from typing import Any


async def cancel_and_await_task(task: asyncio.Task[Any]) -> None:
    if not task.cancel():
        return
    try:
        await task
    except asyncio.CancelledError:
        pass
