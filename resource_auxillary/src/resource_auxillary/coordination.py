"""Common coordination utilities"""

import asyncio
import random


def calculate_exponential_backoff_time(
    cap: float, base: float, attempt: int, *, exponential: int = 2
) -> float:
    return min(cap, base * exponential**attempt)


async def exponential_jittered_backoff(
    cap: float, base: float, attempt: int, *, exponential: int = 2
) -> None:
    await asyncio.sleep(
        random.uniform(
            0,
            calculate_exponential_backoff_time(
                cap, base, attempt, exponential=exponential
            ),
        )  # nosec
    )
