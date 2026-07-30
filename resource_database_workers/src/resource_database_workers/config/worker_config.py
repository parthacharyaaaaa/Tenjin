import tomllib
from typing import Annotated, Any, ClassVar, Final, LiteralString, Mapping, Self

from resource_auxillary.strings import EventName, StreamName

from pydantic import BaseModel, Field

from resource_database_workers.datastructures.streams import (
    STREAM_EVENT_MAPPING,
)

STREAM_KEY: Final[LiteralString] = "STREAMS"
COUNTERS_KEY: Final[LiteralString] = "COUNTERS"
WORKERS_KEY: Final[LiteralString] = "WORKERS"
READERS_KEY: Final[LiteralString] = "READERS"


class CounterWorkersConfig(BaseModel):
    WORKERS_KEY: ClassVar[LiteralString] = "WORKERS"
    RETRY_WORKERS_KEY: ClassVar[LiteralString] = "RETRY_WORKERS"

    WORKER_COUNT: Annotated[int, Field(ge=0, default=0)]
    RETRY_WORKER_COUNT: Annotated[int, Field(ge=0, default=0)]


class StreamWorkersConfig(BaseModel):
    READER_COUNT: Annotated[int, Field(ge=0, default=0)] = 0
    EVENT_WORKER_COUNT_MAPPING: Mapping[
        EventName, Annotated[int, Field(ge=0, default=0)]
    ] = {}


class WorkerCountSettings(BaseModel):
    STREAM_WORKERS_CONFIG: dict[StreamName, StreamWorkersConfig] = {}
    COUNTER_WORKERS_CONFIG: Annotated[CounterWorkersConfig, Field(default_factory=CounterWorkersConfig)]  # type: ignore

    @classmethod
    def construct_from_toml(cls, toml_filepath: str) -> Self:
        instance = cls()  # type: ignore

        with open(toml_filepath, "r") as filepath:
            config_mapping: dict[str, Any] = tomllib.loads(filepath.read())

        # Counter workers
        instance.COUNTER_WORKERS_CONFIG.WORKER_COUNT = config_mapping[WORKERS_KEY][
            COUNTERS_KEY
        ][CounterWorkersConfig.WORKERS_KEY]
        instance.COUNTER_WORKERS_CONFIG.RETRY_WORKER_COUNT = config_mapping[
            WORKERS_KEY
        ][COUNTERS_KEY][CounterWorkersConfig.RETRY_WORKER_COUNT]

        # Stream Readers
        for stream_name in StreamName:
            reader_count: int = config_mapping[READERS_KEY][STREAM_KEY][
                str(stream_name)
            ]
            writer_data: dict[str, int] = config_mapping[WORKERS_KEY][STREAM_KEY][
                str(stream_name)
            ]
            if (
                reader_count
                and not all(writer_data.values())
                or (reader_count == 0 and any(writer_data.values()))
            ):
                raise ValueError(
                    " ".join(
                        (
                            "Invalid worker-reader configuration for stream:",
                            stream_name,
                            "\nEither reader count and all worker counts must be",
                            "zero or non-zero.",
                            f"Reader count: {reader_count}\n",
                            "Workers:\n",
                            ",".join(f"{k}: {v}" for k, v in writer_data.items()),
                        )
                    )
                )

            if not reader_count:
                continue

            event_normalized_counts = {EventName(k): v for k, v in writer_data.items()}
            if not all(
                e in STREAM_EVENT_MAPPING[stream_name]
                for e in event_normalized_counts.keys()
            ):
                raise ValueError(
                    " ".join(
                        (
                            "Unsupported events found in workers for stream",
                            stream_name,
                            "\nSupported events:",
                            ", ".join(STREAM_EVENT_MAPPING[stream_name]),
                        )
                    )
                )

            instance.STREAM_WORKERS_CONFIG[stream_name] = StreamWorkersConfig(
                READER_COUNT=reader_count,
                EVENT_WORKER_COUNT_MAPPING=event_normalized_counts,
            )
        return instance
