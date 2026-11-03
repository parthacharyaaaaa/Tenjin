from datetime import UTC, datetime
from functools import partial
from typing import Annotated, Any

from pydantic import BaseModel, Field

from auth_server.models.events.enums import EventType


class InboxEvent(BaseModel):
    type: EventType
    payload: Annotated[dict[str, Any], Field(default_factory=dict)]
    creation_time: Annotated[
        datetime, Field(default_factory=partial(datetime.now, tzinfo=UTC))
    ]
