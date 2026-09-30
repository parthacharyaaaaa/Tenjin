from collections.abc import Callable
from functools import partial
from typing import Annotated, Any, get_args, get_origin, get_type_hints

from auxillary.dependencies.indicator import Inject


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
            if isinstance(metadata_item, Inject):
                partial_kwargs[param_name] = metadata_item.dependency()
                break

    return partial(worker_callable, **partial_kwargs)
