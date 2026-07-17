from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Self

import orjson
from redis.typing import FieldT, EncodableT


@dataclass(slots=True, init=False)
class DeadCounterBatch:
    table: str
    column: str
    counters: dict[int, int]
    failure_time: datetime

    @classmethod
    def construct_from_event_payload(cls, payload: Mapping[str, Any]) -> Self:
        instance = cls()
        instance.table = payload["table"]
        instance.column = payload["column"]
        instance.failure_time = datetime.fromisoformat(payload["failure_time"])

        counters = orjson.loads(payload["counters"])
        for k, v in counters.items():
            if not (isinstance(k, int) and isinstance(v, int)):
                raise ValueError("Invalid counter data (non-int)")
        instance.counters = counters

        return instance

    @classmethod
    def construct_from_failed_batch(
        cls,
        table: str,
        column: str,
        counters: Mapping[int, int],
        failure_time: datetime | None = None,
    ) -> Self:
        instance = cls()
        instance.table = table
        instance.column = column
        instance.counters = dict(counters)
        instance.failure_time = failure_time or datetime.now()
        return instance

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        return {
            "table": self.table,
            "column": self.column,
            "counters": orjson.dumps(self.counters),
            "failure_time": self.failure_time.isoformat(),
        }

    def __json_repr__(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "column": self.column,
            "counters": self.counters,
            "failure_time": self.failure_time.isoformat(),
        }
