from typing import Annotated

from pydantic import Field


class WorkerStreamReaderMixin:
    CONSUMER_READ_INTERVAL: Annotated[int, Field(ge=0)]
    CONSUMER_READ_SIZE: Annotated[int, Field(ge=1)]
    CONSUMER_BLOCK_TIME: Annotated[int, Field(ge=0)]
    CONSUMER_GROUP_NAME: Annotated[str, Field(frozen=True)]
