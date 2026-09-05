from typing import Annotated

from pydantic import Field

from resource_auxillary.config_mixins.annotations import timedelta_ms


class WorkerInternalQueueMixin:
    IQ_CONSUMER_BASE_WAITING_TIME: timedelta_ms
    IQ_CONSUMER_GET_TIMEOUT: timedelta_ms
    IQ_CONSUMER_SLEEP_INTERVAL: timedelta_ms
    IQ_CONSUMER_BATCH_SIZE_QUOTA: Annotated[int, Field(ge=1)]
