from pathlib import Path
from typing import Annotated, ClassVar

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from resource_database_workers.config import sub_config


class AppConfig(BaseSettings):
    config_filepath: ClassVar[Path] = Path(__file__).parent / "app_config.toml"
    model_config = SettingsConfigDict(toml_file=str(config_filepath))

    WORKER: Annotated[sub_config.WorkerConfig, Field(alias="business")]
    REDIS: Annotated[sub_config.RedisConfig, Field(alias="redis")]
    DATABASE: Annotated[sub_config.DatabaseConfig, Field(alias="database")]

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
