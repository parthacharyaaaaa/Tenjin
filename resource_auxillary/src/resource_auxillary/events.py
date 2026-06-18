from functools import cached_property
from typing import Annotated, Any, Literal, LiteralString, Final

from pydantic import BaseModel, BeforeValidator, Field, ConfigDict

from resource_auxillary.strings import NAME_SEPERATOR, EventName, IntentFlag

EVENTS_TABLE_NAME: Final[LiteralString] = "stream_events"
EVENT_ID_COLUMN_NAME: Final[LiteralString] = "event_id"
EVENT_TIMESTAMP_COLUMN_NAME: Final[LiteralString] = "acknowledgement_time"


class CounterUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    counter_group: str
    cache_key: str  # Cache entry whose counter needs to be updated
    field_name: str  # Field name of cache entry to update with flushed delta
    delta: int


class IntentUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent_name: str
    intent_flag: IntentFlag
    intent_id: str

    @cached_property
    def intent_value(self) -> str:
        return NAME_SEPERATOR.join((self.intent_flag, self.intent_id))


class CacheUpdate(BaseModel):
    cache_key: str
    operation: Literal["invalidate", "mark_missing"]


class EventSideEffects(BaseModel):
    counter_updates: Annotated[tuple[CounterUpdate, ...], Field(default_factory=tuple)]
    intent_updates: Annotated[tuple[IntentUpdate, ...], Field(default_factory=tuple)]
    cache_invalidations: Annotated[
        tuple[CacheUpdate, ...], Field(default_factory=tuple)
    ]


class Event(BaseModel):
    name: Annotated[
        EventName, Field(frozen=True), BeforeValidator(lambda x: x.strip().upper())
    ]

    event_id: Annotated[str, Field(frozen=True)]
    created_at: Annotated[float, Field(frozen=True, ge=0)]

    payload: dict[str, Any]

    side_effects: Annotated[EventSideEffects, Field(frozen=True)]

    @property
    def resource_name(self) -> str:
        return self.name.value
