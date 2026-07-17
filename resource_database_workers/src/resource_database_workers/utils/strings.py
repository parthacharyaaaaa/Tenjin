import time
from typing import Final, LiteralString

from resource_auxillary.strings import NAME_SEPERATOR
import random

INTERNAL_NAME_SEPERATOR: Final[LiteralString] = "-"
assert INTERNAL_NAME_SEPERATOR != NAME_SEPERATOR


def derive_lock_key(name: str) -> str:
    return INTERNAL_NAME_SEPERATOR.join(("lock", name))


# Assuming NAME_SEPARATOR=':',
# counter batch names follow the convention
# counter_name:identifier:version,
# where version and identifier are optional.
# Example: post_votes, and it's next retry counterpart
# being post_votes:T:1, where T is unique identifier


def _generate_batch_identifier(
    *, timestamp: float | None = None, random_suffix_length: int = 8
) -> str:
    return "".join(
        (
            str(int(timestamp or time.time())),
            random.randbytes(random_suffix_length).hex(),
        )
    )


def generate_retry_batch_name(
    counter_name: str, version: int = 0, identifier: str | None = None
) -> str:
    """
    Create a counter retry batch's name
    """
    return INTERNAL_NAME_SEPERATOR.join(
        (counter_name, identifier or _generate_batch_identifier(), str(version))
    )


def extract_batch_metadata(batch: str) -> tuple[str, str | None, int]:
    """
    Extract group name, identifier, and version from a counter batch's name
    """
    if len(split := INTERNAL_NAME_SEPERATOR.split(batch)) != 3:
        return split[0], None, 0
    return split[0], split[1], int(split[2])


def bump_retry_counter(retry_batch: str) -> str:
    """
    Increment the 'retry' part of a counter batch's name
    """
    counter_name, identifier, version = retry_batch.split(INTERNAL_NAME_SEPERATOR)
    return INTERNAL_NAME_SEPERATOR.join(
        (counter_name, identifier, str(int(version) + 1))
    )
