import tomllib
from typing import Annotated, Any, ClassVar, MutableMapping, Self

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    TomlConfigSettingsSource,
)
from resource_auxillary.strings import EventName


class WorkerCountConfig(BaseSettings):
    READERS_KEY: ClassVar[str] = "READERS"
    WORKERS_KEY: ClassVar[str] = "WORKERS"

    READER_COUNT: Annotated[int, Field(ge=1)]
    EVENT_WORKER_COUNT_MAPPING: dict[EventName, Annotated[int, Field(ge=1)]] = {}

    @staticmethod
    def normalize_config_mapping(d: MutableMapping[str, int]) -> dict[EventName, int]:
        return {EventName(event): count for event, count in d.items()}

    @classmethod
    def construct_from_toml(cls, toml_filepath: str) -> Self:
        with open(toml_filepath, "r", encoding="utf-8") as toml_file:
            config_mapping: dict[str, Any] = tomllib.loads(toml_file.read())
            reader_count: int | None = config_mapping[cls.READERS_KEY].get("COUNT")
            stream_mapping: dict[str, int] | None = config_mapping.get(cls.WORKERS_KEY)
        if not reader_count:
            raise KeyError("No reader count found for email workers")
        if not stream_mapping:
            raise KeyError("No worker count found for email workers")

        return cls(
            READER_COUNT=reader_count,
            EVENT_WORKER_COUNT_MAPPING=cls.normalize_config_mapping(stream_mapping),
        )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (TomlConfigSettingsSource(settings_cls),)
