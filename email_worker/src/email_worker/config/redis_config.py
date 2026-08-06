from pathlib import Path
from typing import Annotated, ClassVar

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


class RedisConfig(BaseSettings):
    config_filepath: ClassVar[Path] = Path(__file__).parent / "redis_config.toml"
    model_config = SettingsConfigDict(toml_file=str(config_filepath))

    HOSTNAME: str
    PORT: Annotated[int, Field(ge=1024, le=65_535)]
    DB: Annotated[int, Field(ge=0)]
    DECODE_RESPONSES: Annotated[bool, Field(default=True)]

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
