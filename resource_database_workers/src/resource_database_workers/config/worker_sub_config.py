from typing import Annotated

from pydantic import BaseModel, Field


class BaseWorkerModel(BaseModel):
    @property
    def dormant(self) -> bool:
        return any(self.model_dump().values())


class DownstreamEntityProcessors(BaseWorkerModel):
    STRONG_INSERTIONS: Annotated[int, Field(ge=0, default=0)]
    WEAK_INSERTIONS: Annotated[int, Field(ge=0, default=0)]
    DELETIONS: Annotated[int, Field(ge=0, default=0)]


class UpstreamEntityProcessors(DownstreamEntityProcessors):
    DOWNSTREAM_DELETIONS: Annotated[int, Field(ge=0, default=0)]
    DOWNSTREAM_COUNTER_DECREMENTS: Annotated[int, Field(ge=0, default=0)]


class UserDeletionProcessors(BaseWorkerModel):
    USER_CLEANUP: Annotated[int, Field(ge=0, default=0)]


class CounterProcessors(BaseWorkerModel):
    COUNTERS: Annotated[int, Field(ge=0, default=0)]


class DeadLetterQueueProcessors(BaseWorkerModel):
    STANDARD_DLQ: Annotated[int, Field(ge=0, default=0)]
    COUNTERS_DLQ: Annotated[int, Field(ge=0, default=0)]
