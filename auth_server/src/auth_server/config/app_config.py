from pathlib import Path
from typing import Annotated, ClassVar

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from auth_server.src.auth_server.config import sub_config

__all__ = ("AppConfig",)


class AppConfig(BaseSettings):
    config_filepath: ClassVar[Path] = Path(__file__).parent / "app_config.toml"
    model_config = SettingsConfigDict(toml_file=str(config_filepath))

    CORE: Annotated[sub_config.CoreConfigModel, Field(alias="core")]
    JWKS: Annotated[sub_config.JWKSConfigModel, Field(alias="jwks")]
    REDIS: Annotated[sub_config.RedisConfigModel, Field(alias="redis")]
    DATABASE: Annotated[sub_config.DatabaseConfigModel, Field(alias="database")]
    KEYS: Annotated[sub_config.KeyConfigModel, Field(alias="keys")]
    ADMIN: Annotated[sub_config.AdminConfigModel, Field(alias="admin")]

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
