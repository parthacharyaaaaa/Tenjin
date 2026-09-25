from typing import Annotated

from auxillary.mixins.annotations import timedelta_ms
from pydantic import Field


class WorkerInternalQueueMixin:
    IQ_CONSUMER_BASE_WAITING_TIME: timedelta_ms
    IQ_CONSUMER_GET_TIMEOUT: timedelta_ms
    IQ_CONSUMER_SLEEP_INTERVAL: timedelta_ms
    IQ_CONSUMER_BATCH_SIZE_QUOTA: Annotated[int, Field(ge=1)]
