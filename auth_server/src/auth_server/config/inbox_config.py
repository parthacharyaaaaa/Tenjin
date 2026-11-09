from pathlib import Path
from typing import Annotated, ClassVar, Self

from auxillary.mixins.annotations import timedelta_ms
from pydantic import ConfigDict, Field, model_validator
from pydantic.main import BaseModel
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


class GeneralInboxWorkerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    MAXIMUM_BACKOFF_INTERVAL: timedelta_ms
    BASE_BACKOFF_INTERVAL: timedelta_ms
    BACKOFF_EXPONENTIAL: Annotated[int, Field(ge=1, default=2)]
    LEASE_DURATION: timedelta_ms
    MAX_ATTEMPTS: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def validate_backoff_values(self) -> Self:
        if self.BASE_BACKOFF_INTERVAL > self.MAXIMUM_BACKOFF_INTERVAL:
            raise ValueError(
                " ".join(
                    (
                        f"Base backoff value {self.BASE_BACKOFF_INTERVAL}",
                        "cannot be greater than maximum backoff interval",
                        str(self.MAXIMUM_BACKOFF_INTERVAL),
                    )
                )
            )
        return self


class InboxWorkerSettings(BaseSettings):
    config_filepath: ClassVar[Path] = Path(__file__).parent / "inbox_config.toml"
    model_config = SettingsConfigDict(toml_file=str(config_filepath), frozen=True)

    JWKS_ANNOUNCER: Annotated[
        GeneralInboxWorkerConfig, Field(frozen=True, alias="jwks_announcer")
    ]

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
