import tomllib
from typing import (
    Annotated,
    Any,
    ClassVar,
    Final,
    LiteralString,
    Mapping,
    MutableMapping,
    Self,
)

from resource_auxillary.strings import EventName, StreamName

from pydantic import BaseModel, Field, model_validator

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

    WORKER_COUNT: Annotated[int, Field(ge=1)]
    RETRY_WORKER_COUNT: Annotated[int, Field(ge=1)]

    @classmethod
    def construct_from_toml(cls, toml_filepath: str) -> Self:
        with open(toml_filepath, "r", encoding="utf-8") as toml_file:
            counters_mapping: dict[str, int] = tomllib.loads(toml_file.read())[
                WORKERS_KEY
            ][COUNTERS_KEY]
        return cls(
            WORKER_COUNT=counters_mapping[cls.WORKERS_KEY],
            RETRY_WORKER_COUNT=counters_mapping[cls.RETRY_WORKERS_KEY],
        )


class StreamWorkersConfig(BaseModel):
    STREAM: StreamName
    READER_COUNT: Annotated[int, Field(ge=1)]
    EVENT_WORKER_COUNT_MAPPING: Mapping[EventName, Annotated[int, Field(ge=1)]] = {}

    @model_validator(mode="after")
    def validate_event_mapping(self) -> Self:
        expected_set, actual_set = set(STREAM_EVENT_MAPPING[self.STREAM]), set(
            self.EVENT_WORKER_COUNT_MAPPING
        )
        if missing_events := expected_set - actual_set:
            raise ValueError(
                " ".join(
                    (
                        f"Missing events for stream: {self.STREAM}",
                        ", ".join(missing_events),
                    )
                )
            )
        if unexpected_events := actual_set - expected_set:
            raise ValueError(
                " ".join(
                    (
                        f"Incompatible events specified for stream: {self.STREAM}",
                        ", ".join(unexpected_events),
                    )
                )
            )
        return self

    @staticmethod
    def normalize_config_mapping(d: MutableMapping[str, int]) -> dict[EventName, int]:
        return {EventName(event): count for event, count in d.items()}

    @classmethod
    def construct_from_toml(cls, toml_filepath: str, stream_name: StreamName) -> Self:
        with open(toml_filepath, "r", encoding="utf-8") as toml_file:
            config_mapping: dict[str, Any] = tomllib.loads(toml_file.read())
            reader_count: int | None = config_mapping[READERS_KEY][STREAM_KEY].get(
                stream_name
            )
            stream_mapping: dict[str, int] | None = config_mapping.get(stream_name)
        if not reader_count:
            raise KeyError("No reader count found for stream:", stream_name)
        if not stream_mapping:
            raise KeyError("No worker count found for stream found:", stream_name)

        return cls(
            STREAM=stream_name,
            READER_COUNT=reader_count,
            EVENT_WORKER_COUNT_MAPPING=cls.normalize_config_mapping(stream_mapping),
        )
