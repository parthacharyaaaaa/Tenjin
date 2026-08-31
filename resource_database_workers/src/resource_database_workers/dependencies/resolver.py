from functools import partial
from resource_database_workers.dependencies.indicator import Inject
from typing import Annotated
from collections.abc import Callable, Mapping
from typing import Any, get_type_hints, get_origin, get_args

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


def inject_worker_dependencies(
    worker_callable: Callable[..., Any], context: dict[Any, Any] | None = None
) -> partial[Callable[[], Any]]:
    context = context or {}
    partial_kwargs: dict[str, Any] = {}

    for param_name, type_hint in get_type_hints(
        worker_callable, include_extras=True
    ).items():
        if get_origin(type_hint) != Annotated:
            continue

        if context_dependency := context.get(type_hint):
            partial_kwargs[param_name] = context_dependency
            continue

        _base_type, *metadata = get_args(type_hint)
        for metadata_item in metadata:
            if isinstance(metadata_item, Inject):  # Global dependency
                partial_kwargs[param_name] = metadata_item.dependency()
                break

    return partial(worker_callable, **partial_kwargs)
