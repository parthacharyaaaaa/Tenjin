from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Any, Mapping, Self


@dataclass(slots=True, init=False)
class DeadCounterBatch:
    table: str
    column: str
    counters: dict[int, int]
    failure_time: datetime = field(default_factory=datetime.now)

    @classmethod
    def construct_from_event_payload(cls, payload: Mapping[str, Any]) -> Self:
        instance = cls()
        for dataclass_field in fields(cls):
            setattr(instance, dataclass_field.name, payload[dataclass_field.name])
        return instance
