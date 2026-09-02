from collections import defaultdict
import tomllib
from typing import (
    Annotated,
    Any,
    ClassVar,
    Final,
    LiteralString,
    Self,
)

from resource_auxillary.strings import EventName, StreamName

from pydantic import BaseModel, Field, model_validator

# Strings mapping to key names in config TOML file
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
    STREAM_READER_COUNT_MAPPING: dict[StreamName, Annotated[int, Field(ge=0)]] = {}
    EVENT_WORKER_COUNT_MAPPING: dict[
        StreamName, dict[EventName, Annotated[int, Field(ge=0)]]
    ] = {}

    @classmethod
    def construct_from_toml(cls, toml_filepath: str) -> Self:
        reader_mapping: dict[StreamName, int] = {}
        worker_mapping: defaultdict[StreamName, dict[EventName, int]] = defaultdict(
            dict
        )
        with open(toml_filepath, "r", encoding="utf-8") as toml_file:
            config_mapping: dict[str, Any] = tomllib.loads(toml_file.read())
            readers_context: dict[StreamName, int] = config_mapping[READERS_KEY][
                STREAM_KEY
            ]
            for _stream_name, count in readers_context.items():
                try:
                    reader_mapping[StreamName[_stream_name]] = count
                except ValueError as e:
                    raise ValueError(f"Invalid stream name: {_stream_name}") from e
                if count < 0:
                    raise ValueError(
                        f"Got negative reader count for stream '{_stream_name}'"
                    )

            workers_context: dict[StreamName, dict[str, int]] = config_mapping[
                WORKERS_KEY
            ][STREAM_KEY]
            for _stream_name, worker_count_data in workers_context.items():
                try:
                    stream_name: StreamName = StreamName(_stream_name)
                except ValueError as e:
                    raise ValueError(f"Invalid stream name: {_stream_name}") from e

                for event_name, worker_count in worker_count_data.items():
                    try:
                        worker_mapping[stream_name][EventName(event_name)] = count
                    except ValueError as e:
                        raise ValueError(
                            f"Invalid event name in {stream_name}: {event_name}"
                        ) from e
                    if count < 0:
                        raise ValueError(
                            f"Got negative reader count for event {event_name} in stream '{stream_name}'"
                        )

        return cls(
            STREAM_READER_COUNT_MAPPING=reader_mapping,
            EVENT_WORKER_COUNT_MAPPING=worker_mapping,
        )

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if not any(self.STREAM_READER_COUNT_MAPPING.values()):
            raise ValueError("No stream readers specified")
        if not any(
            any(count_data.values())
            for count_data in self.EVENT_WORKER_COUNT_MAPPING.values()
        ):
            raise ValueError("No stream workers specified")

        valid_writer_data: set[StreamName] = set()
        for stream_name, reader_count in self.STREAM_READER_COUNT_MAPPING.items():
            corresponding_write_data: dict[EventName, int] | None = (
                self.EVENT_WORKER_COUNT_MAPPING.get(stream_name)
            )
            if not corresponding_write_data:
                raise KeyError(f"Missing write data for stream: {stream_name}")
            if reader_count == 0 and any(corresponding_write_data.keys()):
                raise ValueError(
                    f"Orphaned writers found for stream: {stream_name}, context: {corresponding_write_data}"
                )
            elif reader_count != 0 and not all(corresponding_write_data.keys()):
                raise ValueError(
                    f"Event workers missing for stream: {stream_name}, context: {corresponding_write_data}"
                )
            valid_writer_data.add(stream_name)
        if (
            writer_data_residue := self.EVENT_WORKER_COUNT_MAPPING.keys()
            - valid_writer_data
        ):
            raise ValueError(
                f"Found orphaned and possibly extra stream worker data for streams: {','.join(writer_data_residue)}"
            )

        return self
