from typing import Final, LiteralString

INTERNAL_NAME_SEPERATOR: Final[LiteralString] = "-"


def generate_worker_name(
    task_name: str, index: int, *, base_name: str | None = None
) -> str:
    if base_name:
        return INTERNAL_NAME_SEPERATOR.join((base_name, task_name, str(index)))
    return INTERNAL_NAME_SEPERATOR.join((task_name, str(index)))
