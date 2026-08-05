from pathlib import Path
from typing import Annotated, ClassVar

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from resource_auxillary import config_mixins


class EmailWorkerConfig(
    config_mixins.WorkerStreamReaderMixin,
    config_mixins.WorkerInternalQueueMixin,
    config_mixins.WorkerRetryMixin,
    config_mixins.WorkerReclaimMixin,
    config_mixins.WorkerDLQMixin,
):
    GRACEFUL_SHUTDOWN_PERIOD: Annotated[float, Field(ge=0)]

    SMTP_NETWORK_ERROR_WINDOW: Annotated[int, Field(ge=1)]
    MAXIMUM_SMTP_REFRESHES: Annotated[int, Field(ge=0)]


class EmailConfig(BaseSettings):
    config_filepath: ClassVar[Path] = Path(__file__).parent / "email_config.toml"
    model_config = SettingsConfigDict(toml_file=str(config_filepath))

    # Network Identification
    hostname: str
    port: Annotated[int, Field(ge=1024, le=65_535)]

    # Security
    use_tls: Annotated[bool, Field(default=True)]

    # Worker sub-config
    WORKER: Annotated[EmailWorkerConfig, Field(alias="worker")]

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
