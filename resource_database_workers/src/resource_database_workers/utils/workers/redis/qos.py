"""Quality-of-Service utility functions"""

from typing import Any, Callable, Coroutine, Sequence

from redis.asyncio import Redis
from redis.exceptions import RedisError, ExceptionType

from resource_auxillary.events import StreamedEvent
from resource_auxillary.strings import StreamName

from resource_database_workers.config.sub_config import WorkerConfig
from resource_database_workers.utils.coordination import exponential_jittered_backoff
from resource_database_workers.utils.workers.redis.post_processing import amortize_event


async def execute_with_redis_retries(
    worker_config: WorkerConfig,
    redis_coroutine: Callable[[], Coroutine[Any, Any, Any]],
    attempts: int,
) -> Any:
    exception: Exception | None = None
    for _attempt in range(1, attempts + 1):
        try:
            return await redis_coroutine()
        except RedisError as redis_error:
            exception = redis_error
            if redis_error.error_type == ExceptionType.NETWORK:
                await exponential_jittered_backoff(
                    worker_config.MAXIMUM_BACKOFF_INTERVAL,
                    worker_config.BASE_BACKOFF_INTERVAL,
                    _attempt,
                    exponential=worker_config.BACKOFF_EXPONENTIAL,
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
    worker_config: WorkerConfig,
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
        await execute_with_redis_retries(worker_config, redis_coroutine, attempts)
    except Exception as e:
        dlq_attempts: int = (
            1 if getattr(e, "error_type", None) == ExceptionType.NETWORK else attempts
        )
        coro = lambda: amortize_event(
            redis, events, event_stream_name, group_name, dlq_stream_name
        )
        await execute_with_redis_retries(worker_config, coro, dlq_attempts)
