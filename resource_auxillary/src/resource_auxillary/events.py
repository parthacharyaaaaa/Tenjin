from datetime import UTC, datetime
from functools import cached_property, partial
from typing import Annotated, Any, Literal, Self

import orjson
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from redis.typing import EncodableT, FieldT

from resource_auxillary.strings import (
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


class CacheUpdate(BaseModel):
    cache_key: str
    operation: Literal["invalidate", "mark_missing"]
    resource_type: Literal["string", "mapping"] = Field(default="mapping")

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        return {
            "cache_key": self.cache_key,
            "operation": self.operation,
            "resource_type": self.resource_type,
        }


class CacheSideEffects(BaseModel):
    counter_updates: Annotated[tuple[CounterUpdate, ...], Field(default_factory=tuple)]
    intent_updates: Annotated[tuple[IntentUpdate, ...], Field(default_factory=tuple)]
    cache_invalidations: Annotated[
        tuple[CacheUpdate, ...], Field(default_factory=tuple)
    ]

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        return {
            "counter_updates": orjson.dumps(
                [i.model_dump(mode="json") for i in self.counter_updates]
            ),
            "intent_updates": orjson.dumps(
                [i.model_dump(mode="json") for i in self.intent_updates]
            ),
            "cache_invalidations": orjson.dumps(
                [i.model_dump(mode="json") for i in self.cache_invalidations]
            ),
        }


class EventSideEffects(BaseModel):
    cache: Annotated[CacheSideEffects, Field(default_factory=CacheSideEffects)]

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        return {"cache": orjson.dumps(self.cache.model_dump(mode="json"))}


class Event(BaseModel):
    name: Annotated[
        EventName, Field(frozen=True), BeforeValidator(lambda x: x.strip().upper())
    ]
    payload: Annotated[dict[str, Any], Field(default_factory=dict)]
    side_effects: Annotated[
        EventSideEffects, Field(frozen=True, default_factory=EventSideEffects)
    ]
    creation_time: Annotated[
        datetime, Field(frozen=True, default_factory=partial(datetime.now, tz=UTC))
    ]

    @property
    def resource_name(self) -> str:
        return self.name.value

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        return {
            "name": self.name,
            "payload": orjson.dumps(self.payload),
            "side_effects": orjson.dumps(self.side_effects.model_dump(mode="json")),
            "creation_time": self.creation_time.isoformat(),
        }

    @classmethod
    def reconstruct_from_stream(cls, stream_entry: dict[str, str]) -> Self:
        try:
            return cls(
                name=stream_entry["name"],
                payload=orjson.loads(stream_entry["payload"]),
                side_effects=EventSideEffects.model_validate(
                    orjson.loads(stream_entry["side_effects"])
                ),
                creation_time=datetime.fromisoformat(stream_entry["creation_time"]),
            )
        except (KeyError, ValueError) as e:
            raise ValueError(f"Malformed stream entry: {e}") from e


class StreamedEvent(Event):
    """Event that has been streamed to Redis and is assosciated with a unique, chronological ID"""

    event_id: str

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        return super(StreamedEvent, self).__cache_repr__() | {"event_id": self.event_id}

    @classmethod
    def construct_from_stream_record(cls, stream: tuple[str, dict[str, str]]) -> Self:
        stream_id, stream_entry = stream
        base_event: Event = Event.reconstruct_from_stream(stream_entry)
        return cls(
            event_id=stream_id,
            name=base_event.name,
            payload=base_event.payload,
            side_effects=base_event.side_effects,
            creation_time=base_event.creation_time,
        )

    @classmethod
    def safe_construct_from_malformed_stream(
        cls, stream_entry: tuple[str, dict[str, str]]
    ) -> Self:
        return cls(
            name=EventName.MALFORMED,
            payload=stream_entry[1],
            event_id=stream_entry[0],
            side_effects=EventSideEffects(),
        )
