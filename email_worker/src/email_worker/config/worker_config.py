from typing import Annotated, Self

from pydantic import Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    TomlConfigSettingsSource,
)


class WorkerCountConfig(BaseSettings):
    USER_DELETION_EMAIL: Annotated[int, Field(ge=0)]
    USER_REGISTRATION_EMAIL: Annotated[int, Field(ge=0)]
    USER_PASSWORD_RECOVERY_EMAIL: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def validate_worker_counts(self) -> Self:
        if all(i == 0 for i in self.__dataclass_fields__.values()):
            raise ValueError("No worker tasks specified")
        return self

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
