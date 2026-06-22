from functools import cached_property
import time
from typing import Annotated, Any, Literal, Self
from uuid import uuid4

from auxillary.utils import cache_repr, json_repr
import orjson

from pydantic import BaseModel, BeforeValidator, Field, ConfigDict

from redis.typing import FieldT, EncodableT

from resource_auxillary.strings import (
    MALFORMED_EVENT_PREFIX,
    NAME_SEPERATOR,
    EventName,
    IntentFlag,
)


class CounterUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    counter_group: str
    cache_key: str  # Cache entry whose counter needs to be updated
    field_name: str  # Field name of cache entry to update with flushed delta
    delta: int

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        return {
            "counter_group": self.counter_group,
            "cache_key": self.cache_key,
            "field_name": self.field_name,
            "delta": self.delta,
        }


class IntentUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent_name: str
    intent_flag: IntentFlag
    intent_id: str

    @cached_property
    def intent_value(self) -> str:
        return NAME_SEPERATOR.join((self.intent_flag, self.intent_id))

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        return {
            "intent_name": self.intent_name,
            "intent_flag": self.intent_flag.value,
            "intent_id": self.intent_id,
        }

    def __json_repr__(self) -> dict[str, Any]:
        return {
            "intent_name": self.intent_name,
            "intent_flag": self.intent_flag.value,
            "intent_id": self.intent_id,
        }


class CacheUpdate(BaseModel):
    cache_key: str
    operation: Literal["invalidate", "mark_missing"]

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        return {
            "cache_key": self.cache_key,
            "operation": self.operation,
        }

    def __json_repr__(self) -> dict[str, Any]:
        return {
            "cache_key": self.cache_key,
            "operation": self.operation,
        }


class EventSideEffects(BaseModel):
    counter_updates: Annotated[tuple[CounterUpdate, ...], Field(default_factory=tuple)]
    intent_updates: Annotated[tuple[IntentUpdate, ...], Field(default_factory=tuple)]
    cache_invalidations: Annotated[
        tuple[CacheUpdate, ...], Field(default_factory=tuple)
    ]

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        return {
            "counter_updates": orjson.dumps(
                [cache_repr(i) for i in self.counter_updates]
            ),
            "intent_updates": orjson.dumps(
                [cache_repr(i) for i in self.intent_updates]
            ),
            "cache_invalidations": orjson.dumps(
                [cache_repr(i) for i in self.cache_invalidations]
            ),
        }

    def __json_repr__(self) -> dict[str, Any]:
        return {
            "counter_updates": list(self.counter_updates),
            "intent_updates": list(self.intent_updates),
            "cache_invalidations": list(self.cache_invalidations),
        }


class Event(BaseModel):
    name: Annotated[
        EventName, Field(frozen=True), BeforeValidator(lambda x: x.strip().upper())
    ]

    event_id: Annotated[str, Field(frozen=True, default_factory=time.time_ns)]
    created_at: Annotated[float, Field(frozen=True, ge=0, default=time.time_ns)]

    payload: dict[str, Any]

    side_effects: Annotated[EventSideEffects, Field(frozen=True)]

    @property
    def resource_name(self) -> str:
        return self.name.value

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        return {
            "name": self.name,
            "event_id": self.event_id,
            "created_at": self.created_at,
            "payload": orjson.dumps(self.payload),
            "side_effects": orjson.dumps(cache_repr(self.side_effects)),
        }

    def __json_repr__(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "event_id": self.event_id,
            "created_at": self.created_at,
            "payload": self.payload,
            "side_effects": json_repr(self.side_effects),
        }

    @classmethod
    def safe_construct_from_malformed_stream(cls, stream_entry: dict[str, str]) -> Self:
        creation_time = time.time()

        if stream_creation_time := stream_entry.get("created_at"):
            splits: list[str] = stream_creation_time.split(".")
            if len(splits) == 2 and all(s.isnumeric() for s in splits):
                creation_time = float(stream_creation_time)

        return Event(
            name=EventName.MALFORMED,
            event_id=stream_entry.get(
                "event_id", NAME_SEPERATOR.join((MALFORMED_EVENT_PREFIX, uuid4().hex))
            ),
            created_at=creation_time,
            payload=stream_entry,
            side_effects=EventSideEffects(),  # type: ignore[reportCallIssue]
        )
