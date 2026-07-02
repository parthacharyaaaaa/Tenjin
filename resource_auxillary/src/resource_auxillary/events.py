from functools import cached_property
from typing import Annotated, Any, Literal, Self

from auxillary.utils import cache_repr, json_repr
import orjson

from pydantic import BaseModel, BeforeValidator, Field, ConfigDict

from redis.typing import FieldT, EncodableT

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

    def __json_repr__(self) -> dict[str, Any]:
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
    payload: dict[str, Any]
    side_effects: Annotated[EventSideEffects, Field(frozen=True)]

    @property
    def resource_name(self) -> str:
        return self.name.value

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        return {
            "name": self.name,
            "payload": orjson.dumps(self.payload),
            "side_effects": orjson.dumps(cache_repr(self.side_effects)),
        }

    def __json_repr__(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "payload": self.payload,
            "side_effects": json_repr(self.side_effects),
        }

    @classmethod
    def reconstruct_from_stream(cls, stream_entry: dict[str, str]) -> Self:
        try:
            name: EventName = EventName(stream_entry["name"])
            payload: dict[str, Any] = orjson.loads(stream_entry["payload"])
            raw_side_effects: dict[str, Any] = orjson.loads(
                stream_entry["side_effects"]
            )

            counter_updates = tuple(
                CounterUpdate(**cu)
                for cu in orjson.loads(raw_side_effects.get("counter_updates", b"[]"))
            )
            intent_updates = tuple(
                IntentUpdate(**iu)
                for iu in orjson.loads(raw_side_effects.get("intent_updates", b"[]"))
            )
            cache_invalidations = tuple(
                CacheUpdate(**ci)
                for ci in orjson.loads(
                    raw_side_effects.get("cache_invalidations", b"[]")
                )
            )

            return cls(
                name=name,
                payload=payload,
                side_effects=EventSideEffects(
                    counter_updates=counter_updates,
                    intent_updates=intent_updates,
                    cache_invalidations=cache_invalidations,
                ),
            )
        except (KeyError, ValueError, orjson.JSONDecodeError) as e:
            raise ValueError(f"Malformed stream entry: {e}") from e


class StreamedEvent(Event):
    """Event that has been streamed to Redis and is assosciated with a unique, chronological ID"""

    event_id: int

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        return super().__cache_repr__() | {"event_id": self.event_id}

    def __json_repr__(self) -> dict[str, Any]:
        return super().__json_repr__() | {"event_id": self.event_id}

    @classmethod
    def construct_from_stream_record(cls, stream: tuple[str, dict[str, str]]) -> Self:
        stream_id, stream_entry = stream
        event_id: int = int(stream_id.replace("-", ""))
        base_event: Event = Event.reconstruct_from_stream(stream_entry)
        return cls(
            event_id=event_id,
            name=base_event.name,
            payload=base_event.payload,
            side_effects=base_event.side_effects,
        )

    @classmethod
    def safe_construct_from_malformed_stream(
        cls, stream_entry: tuple[str, dict[str, str]]
    ) -> Self:
        event_id: int = int(stream_entry[0].replace("-", ""))

        return StreamedEvent(
            name=EventName.MALFORMED,
            payload=stream_entry[1],
            event_id=event_id,
            side_effects=EventSideEffects(),  # type: ignore[reportCallIssue]
        )
