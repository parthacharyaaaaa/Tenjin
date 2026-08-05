"""QoS utility components"""

from typing import Annotated, Self

from pydantic import BeforeValidator, Field, model_validator
from resource_auxillary.strings import StreamName


class WorkerRetryMixin:
    MAXIMUM_BACKOFF_INTERVAL: Annotated[int, Field(ge=0)]
    BASE_BACKOFF_INTERVAL: Annotated[int, Field(ge=0)]
    BACKOFF_EXPONENTIAL: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def validate_backoff_values(self) -> Self:
        if self.BASE_BACKOFF_INTERVAL > self.MAXIMUM_BACKOFF_INTERVAL:
            raise ValueError(
                " ".join(
                    (
                        f"Base backoff value {self.BASE_BACKOFF_INTERVAL}",
                        "cannot be greater than maximum backoff interval",
                        str(self.MAXIMUM_BACKOFF_INTERVAL),
                    )
                )
            )
        return self


class WorkerReclaimMixin:
    RECLAIM_THRESHOLD: Annotated[int, Field(ge=1)]
    RECLAIMATION_CHECK_INTERVAL: Annotated[int, Field(ge=1)]
    MAX_DELIVERIES: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def validate_reclamation_interval(self) -> Self:
        if self.RECLAIMATION_CHECK_INTERVAL > self.RECLAIM_THRESHOLD:
            raise ValueError(
                " ".join(
                    (
                        f"Reclaim threshold {self.RECLAIM_THRESHOLD}",
                        "cannot be lesser than reclaimation check interval",
                        str(self.RECLAIMATION_CHECK_INTERVAL),
                    )
                )
            )
        return self


class WorkerDLQMixin:
    MAX_RETRIES: Annotated[int, Field(ge=0)]
    DLQ_NAME: Annotated[StreamName, BeforeValidator(lambda x: x.strip())]
