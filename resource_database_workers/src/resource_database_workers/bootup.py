import asyncio
from typing import Callable

from resource_database_workers.datastructures.worker_inputs import (
    COUNTER_WORKER_INPUT,
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
from resource_database_workers.src.resource_database_workers.dependencies import (
    get_queue_registry,
)
from resource_database_workers.src.resource_database_workers.utils.strings import (
    generate_worker_name,
)
from resource_database_workers.src.resource_database_workers.utils.typing import (
    GenericDataclass,
)
from resource_database_workers.tasks.counters import (
    batch_update_counters,
)
from resource_database_workers.tasks.counters import (
    batch_update_counters,
    batch_update_retry_counters,
)


async def counter_worker_wrapper(
    counter_config: CounterWorkersConfig,
) -> None:
    async with asyncio.TaskGroup() as tg:
        for _ in range(counter_config.WORKER_COUNT):
            tg.create_task(
                batch_update_counters(**COUNTER_WORKER_INPUT.__dataclass_fields__)
            )
        for _ in range(counter_config.RETRY_WORKER_COUNT):
            tg.create_task(
                batch_update_retry_counters(**COUNTER_WORKER_INPUT.__dataclass_fields__)
            )


async def stream_worker_wrapper(stream_config: StreamWorkersConfig) -> None:
    async with asyncio.TaskGroup() as tg:
        reader_callable: Callable = STREAM_CONSUMER_MAPPING[stream_config.STREAM]
        for event, worker_count in stream_config.EVENT_WORKER_COUNT_MAPPING.items():
            worker_callable: Callable = EVENT_WORKER_MAPPING[event]
            worker_arguments: GenericDataclass = WORKER_INPUT_DATA_MAPPING[event]
            for i in range(1, worker_count + 1):
                tg.create_task(
                    worker_callable(**worker_arguments.__dataclass_fields__),
                    name=generate_worker_name(event, i),
                )
        queue_mapping = get_queue_registry().resolve_stream_reader_queue_mapping(
            stream_config.STREAM
        )
        reader_task_input: UpstreamDispatcherInput = UpstreamDispatcherInput(
            stream_name=stream_config.STREAM, queue_mapping=queue_mapping
        )
        for i in range(1, stream_config.READER_COUNT + 1):
            tg.create_task(
                reader_callable(**reader_task_input.__dataclass_fields__),
                name=generate_worker_name(stream_config.STREAM, i, base_name="reader"),
            )


async def spawn_tasks(
    worker_config: CounterWorkersConfig | StreamWorkersConfig,
) -> None:
    if isinstance(worker_config, CounterWorkersConfig):
        await counter_worker_wrapper(worker_config)
    else:
        await stream_worker_wrapper(worker_config)
