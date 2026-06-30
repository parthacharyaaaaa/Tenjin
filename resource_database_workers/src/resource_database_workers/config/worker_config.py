from dataclasses import dataclass
from typing import Any, Mapping, Self

import orjson

from resource_auxillary.strings import EventName


@dataclass(slots=True, init=False)
class WorkerSettings:
    _queue_worker_counts: Mapping[EventName, int]
    _counters: int
    _standard_dlq: int
    _counters_dlq: int
    _upstream_readers: int
    _downstream_readers: int
    _downstream_counter_readers: int

    @property
    def counters(self) -> int:
        return self._counters

    @property
    def standard_dlq(self) -> int:
        return self._standard_dlq

    @property
    def counters_dlq(self) -> int:
        return self._counters_dlq

    @property
    def upstream_readers(self) -> int:
        return self._upstream_readers

    @property
    def downstream_readers(self) -> int:
        return self._downstream_readers

    @property
    def downstream_counter_readers(self) -> int:
        return self._downstream_counter_readers

    @property
    def queue_worker_counts(self) -> Mapping[EventName, int]:
        return self._queue_worker_counts

    @staticmethod
    def check_consumers_integrity(
        entries: Mapping[EventName, int],
        counters: int,
        standard_dlq: int,
        counters_dlq: int,
    ) -> None:
        # DLQ validation
        if not standard_dlq:
            raise ValueError("Non-zero DLQ consumers specified")
        if counters and not counters_dlq:
            raise ValueError(
                " ".join(
                    (
                        f"Non-zero counter workers {counters}",
                        "specified with 0 counters DLQ consumers",
                    )
                )
            )
        if counters_dlq and not counters:
            raise ValueError(
                " ".join(
                    (
                        f"Non-zero counter DLQ workers {counters_dlq}",
                        "specified with 0 counter workers",
                    )
                )
            )

    @classmethod
    def construct_from_json(cls, json_filepath: str) -> Self:
        instance = cls()

        casted_contents: dict[EventName, int] = {}
        with open(json_filepath, "rb") as config_file:
            contents: dict[str, Any] = orjson.loads(config_file.read())

        # Read non-stream worker counts (counters, DLQ)
        counters = int(contents.pop("COUNTERS"))
        standard_dlq = int(contents.pop("STANDARD_DLQ"))
        counters_dlq = int(contents.pop("COUNTERS_DLQ"))

        for k, v in contents.items():
            event: EventName = EventName(k)
            consumer_count: int = int(v)
            if consumer_count < 0:
                raise ValueError(f"Consumer count for event {event} below 0")
            if consumer_count > 0:
                dormant = False
            casted_contents[event] = consumer_count

        cls.check_consumers_integrity(
            casted_contents, counters, standard_dlq, counters_dlq
        )

        instance._queue_worker_counts = casted_contents
        instance._counters = counters
        instance._counters_dlq = counters_dlq
        instance._standard_dlq = standard_dlq

        return instance
