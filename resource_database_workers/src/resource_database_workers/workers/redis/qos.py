"""Quality-of-Service utility functions"""

from contextlib import asynccontextmanager
from typing import Any, Callable, Coroutine, Sequence

from redis.asyncio import Redis
from redis.exceptions import RedisError, ExceptionType

from resource_auxillary.coordination import exponential_jittered_backoff
from resource_auxillary.events import StreamedEvent
from resource_auxillary.strings import StreamName
from resource_auxillary.typing import SupportsExponentialJitteredRetryPolicy

from resource_database_workers.src.resource_database_workers.workers.redis.post_processing import (
    amortize_event,
)


async def execute_with_redis_retries(
    retry_policy: SupportsExponentialJitteredRetryPolicy,
    redis_coroutine: Callable[[], Coroutine[Any, Any, Any]],
    attempts: int | None = None,
) -> Any:
    attempts = attempts or retry_policy.MAX_RETRIES
    exception: Exception | None = None
    for _attempt in range(1, attempts + 1):
        try:
            return await redis_coroutine()
        except RedisError as redis_error:
            exception = redis_error
            if redis_error.error_type == ExceptionType.NETWORK:
                await exponential_jittered_backoff(
                    retry_policy.MAXIMUM_BACKOFF_INTERVAL,
                    retry_policy.BASE_BACKOFF_INTERVAL,
                    _attempt,
                    exponential=retry_policy.BACKOFF_EXPONENTIAL,
                )
                continue

            break
        except Exception as e:
            exception = e
            break

    if exception:
        raise exception


async def dlq_aware_process_events(
    redis: Redis,
    retry_policy: SupportsExponentialJitteredRetryPolicy,
    events: Sequence[StreamedEvent],
    redis_coroutine: Callable[[], Coroutine[Any, Any, Any]],
    attempts: int,
    event_stream_name: StreamName,
    group_name: str,
    dlq_stream_name: StreamName,
) -> Any:
    """
    DLQ-aware event processing helper with retries
    """
    try:
        await execute_with_redis_retries(retry_policy, redis_coroutine, attempts)
    except Exception as e:
        dlq_attempts: int = (
            1 if getattr(e, "error_type", None) == ExceptionType.NETWORK else attempts
        )
        coro = lambda: amortize_event(
            redis, events, event_stream_name, group_name, dlq_stream_name
        )
        await execute_with_redis_retries(retry_policy, coro, dlq_attempts)


@asynccontextmanager
async def locked_operation(redis: Redis, lock_name: str):
    try:
        yield
    finally:
        await redis.delete(lock_name)
