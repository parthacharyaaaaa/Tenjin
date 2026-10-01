import tomllib
from typing import (
    Annotated,
    ClassVar,
    LiteralString,
    Self,
)

from pydantic import BaseModel, Field
from resource_auxillary.config_mixins.stream_worker import StreamWorkersMixin


class CounterWorkersConfig(BaseModel):
    WORKERS_KEY: ClassVar[LiteralString] = "WORKERS"
    RETRY_WORKERS_KEY: ClassVar[LiteralString] = "RETRY_WORKERS"
    COUNTERS_KEY: ClassVar[LiteralString] = "COUNTERS"

    WORKER_COUNT: Annotated[int, Field(ge=1)]
    RETRY_WORKER_COUNT: Annotated[int, Field(ge=1)]

    @classmethod
    def construct_from_toml(cls, toml_filepath: str) -> Self:
        with open(toml_filepath, "r", encoding="utf-8") as toml_file:
            counters_mapping: dict[str, int] = tomllib.loads(toml_file.read())[
                cls.WORKERS_KEY
            ][cls.COUNTERS_KEY]
        return cls(
            WORKER_COUNT=counters_mapping[cls.WORKERS_KEY],
            RETRY_WORKER_COUNT=counters_mapping[cls.RETRY_WORKERS_KEY],
        )


class StreamWorkersConfig(StreamWorkersMixin, BaseModel):
    pass
