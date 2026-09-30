from collections.abc import Callable, Mapping
from functools import partial
from typing import Any

from auxillary.dependencies.resolution import inject_worker_dependencies
from resource_auxillary.strings import EventName

from resource_database_workers.dependencies.event_dependencies import (
    EVENT_WORKER_DATA_MAPPING,
    t_event_worker_data,
)


def inject_stream_worker_dependencies(
    event_name: EventName,
    worker_context: Mapping[Any, Any],
    *,
    worker_data_mapping: Mapping[
        EventName, t_event_worker_data
    ] = EVENT_WORKER_DATA_MAPPING,
) -> partial[Callable[[], Any]]:
    worker_callable, event_context = worker_data_mapping[event_name]
    event_context |= worker_context

    return inject_worker_dependencies(worker_callable, event_context)
