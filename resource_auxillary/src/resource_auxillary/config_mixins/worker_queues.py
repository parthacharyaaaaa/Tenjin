from typing import Annotated

from pydantic import Field


class WorkerInternalQueueMixin:
    IQ_CONSUMER_BASE_WAITING_TIME: Annotated[int, Field(ge=0)]
    IQ_CONSUMER_GET_TIMEOUT: Annotated[int, Field(ge=0)]
    IQ_CONSUMER_BATCH_SIZE_QUOTA: Annotated[int, Field(ge=1)]
    IQ_CONSUMER_SLEEP_INTERVAL: Annotated[int, Field(ge=0)]
