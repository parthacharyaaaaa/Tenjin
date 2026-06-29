from typing import Self

from pydantic import model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from resource_database_workers.config.worker_sub_config import (
    AnimeWorkerSettings,
    CommentWorkerSettings,
    CounterWorkerSettings,
    DeadLetterWorkerSettings,
    DownstreamWorkerSettings,
    ForumWorkerSettings,
    OrphanedWorkerSettings,
    PostWorkerSettings,
    UserWorkerSettings,
)


class WorkerSettings(BaseSettings):
    USER: UserWorkerSettings
    POSTS: PostWorkerSettings
    COMMENTS: CommentWorkerSettings
    ANIMES: AnimeWorkerSettings
    FORUMS: ForumWorkerSettings
    COUNTERS: CounterWorkerSettings
    ORPHANED: OrphanedWorkerSettings
    DOWNSTREAM: DownstreamWorkerSettings
    DLQ: DeadLetterWorkerSettings

    model_config = SettingsConfigDict(
        toml_file="config.toml",
        extra="forbid",
    )

    @model_validator(mode="after")
    def check_dormancy(self) -> Self:
        if all(sub_setting.dormant() for sub_setting in self.model_dump().values()):
            raise ValueError("All consumer types dormant")
        return self

    @model_validator(mode="after")
    def check_counters_dlq(self) -> Self:
        if self.COUNTERS.dormant and self.DLQ.COUNTERS_DLQ != 0:
            raise ValueError(
                " ".join(
                    (
                        f"Counters DLQ workers {self.DLQ.COUNTERS_DLQ} specified",
                        "with 0 counters workers present",
                    )
                )
            )
        elif not self.COUNTERS.dormant and self.DLQ.COUNTERS_DLQ == 0:
            raise ValueError(
                " ".join(
                    (
                        "Non-zero counter workers specified but counters DLQ workers",
                        "not provided",
                    )
                )
            )
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
