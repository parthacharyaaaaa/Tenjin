from datetime import timedelta
from typing import Annotated, Protocol

from auxillary.mixins.annotations import timedelta_ms
from pydantic import Field


class WorkerStreamReaderMixin:
    CONSUMER_READ_INTERVAL: timedelta_ms
    CONSUMER_READ_SIZE: Annotated[int, Field(ge=1)]
    CONSUMER_BLOCK_TIME: timedelta_ms
    CONSUMER_GROUP_NAME: Annotated[str, Field(frozen=True)]


class SupportsWorkerStreamReading(Protocol):
    CONSUMER_READ_INTERVAL: timedelta
    CONSUMER_READ_SIZE: int
    CONSUMER_BLOCK_TIME: timedelta
    CONSUMER_GROUP_NAME: str
