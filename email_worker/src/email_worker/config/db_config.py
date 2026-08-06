from pathlib import Path
from typing import ClassVar

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from auxillary.mixins.db_config import (
    BasicConnectionPoolConfigMixin,
    BasicPostgresDatabaseConfigMixin,
)


class DatabaseConfig(
    BasicPostgresDatabaseConfigMixin, BasicConnectionPoolConfigMixin, BaseSettings
):
    config_filepath: ClassVar[Path] = Path(__file__).parent / "db_config.toml"
    model_config = SettingsConfigDict(toml_file=str(config_filepath))

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
