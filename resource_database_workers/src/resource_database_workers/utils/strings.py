import time

from resource_auxillary.strings import NAME_SEPERATOR


def derive_lock_key(name: str) -> str:
    return NAME_SEPERATOR.join(("lock", name))


def derive_retry_batch_name(
    counter_name: str, version: int = 0, timestamp: float | None = None
) -> str:
    return NAME_SEPERATOR.join(
        (counter_name, str(timestamp or time.time()), str(version))
    )


def derive_counter_group_from_batch(retry_batch: str) -> str:
    return NAME_SEPERATOR.join(NAME_SEPERATOR.split(retry_batch)[:2])


def derive_version_from_batch(retry_batch: str) -> int:
    return int(NAME_SEPERATOR.split(retry_batch)[-2])


def bump_retry_counter(retry_batch: str) -> str:
    counter_name, timestamp, version = retry_batch.split(NAME_SEPERATOR)
    return NAME_SEPERATOR.join((counter_name, timestamp, str(int(version) + 1)))
