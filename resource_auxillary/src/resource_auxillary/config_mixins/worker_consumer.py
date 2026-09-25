from typing import Annotated

from auxillary.mixins.annotations import timedelta_ms
from pydantic import Field


class WorkerStreamReaderMixin:
    CONSUMER_READ_INTERVAL: timedelta_ms
    CONSUMER_READ_SIZE: Annotated[int, Field(ge=1)]
    CONSUMER_BLOCK_TIME: timedelta_ms
    CONSUMER_GROUP_NAME: Annotated[str, Field(frozen=True)]
