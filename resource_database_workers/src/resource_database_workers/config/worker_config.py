from collections import defaultdict
from functools import cached_property
from pathlib import Path
from typing import Annotated, Self, Protocol

from resource_auxillary.strings import EventName, StreamName

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from resource_database_workers.src.resource_database_workers.datastructures.streams import (
    STREAM_EVENT_MAPPING,
)


class EventWorkerConfig(Protocol):
    @cached_property
    def reader_count_mapping(self) -> dict[StreamName, int]: ...


class BaseWorkerConfig(BaseModel):
    @property
    def dormant(self) -> bool:
        return any(v for v in self.model_dump().values())


class StreamReaderConfig(BaseWorkerConfig):
    POSTS: Annotated[int, Field(ge=0, default=0)]
    COMMENTS: Annotated[int, Field(ge=0, default=0)]
    FORUMS: Annotated[int, Field(ge=0, default=0)]
    ANIMES: Annotated[int, Field(ge=0, default=0)]
    USERS: Annotated[int, Field(ge=0, default=0)]
    DOWNSTREAM_DELETIONS: Annotated[int, Field(ge=0, default=0)]
    DOWNSTREAM_COUNTER_DECREMENTS: Annotated[int, Field(ge=0, default=0)]

    @cached_property
    def reader_count_mapping(self) -> dict[StreamName, int]:
        return {
            StreamName.POSTS: self.POSTS,
            StreamName.COMMENTS: self.COMMENTS,
            StreamName.FORUMS: self.FORUMS,
            StreamName.ANIMES: self.ANIMES,
            StreamName.USERS: self.USERS,
            StreamName.DOWNSTREAM_DELETIONS: self.DOWNSTREAM_DELETIONS,
            StreamName.DOWNSTREAM_COUNTER_DECREMENTS: self.DOWNSTREAM_COUNTER_DECREMENTS,
        }


class DeadLetterWorkerConfig(BaseWorkerConfig):
    STANDARD: Annotated[int, Field(ge=0, default=0)]
    COUNTERS: Annotated[int, Field(ge=0, default=0)]


class CounterConfig(BaseWorkerConfig):
    WORKERS: Annotated[int, Field(ge=0, default=0)]


class DownstreamDeletionWorkerConfig(BaseWorkerConfig):
    ORPHANED_POST_DELETE: Annotated[int, Field(ge=0, default=0)]
    ORPHANED_COMMENT_DELETE: Annotated[int, Field(ge=0, default=0)]

    @cached_property
    def worker_count_mapping(self) -> dict[EventName, int]:
        return {
            EventName.ORPHANED_POST_DELETE: self.ORPHANED_POST_DELETE,
            EventName.ORPHANED_COMMENT_DELETE: self.ORPHANED_COMMENT_DELETE,
        }


class DownstreamDecrementWorkerConfig(BaseWorkerConfig):
    DOWNSTREAM_USER_POST_DECREMENT: Annotated[int, Field(ge=0, default=0)]
    DOWNSTREAM_USER_COMMENT_DECREMENT: Annotated[int, Field(ge=0, default=0)]
    DOWNSTREAM_FORUM_POST_DECREMENT: Annotated[int, Field(ge=0, default=0)]
    DOWNSTREAM_POST_COMMENT_DECREMENT: Annotated[int, Field(ge=0, default=0)]

    @cached_property
    def worker_count_mapping(self) -> dict[EventName, int]:
        return {
            EventName.DOWNSTREAM_USER_POST_DECREMENT: self.DOWNSTREAM_USER_POST_DECREMENT,
            EventName.DOWNSTREAM_USER_COMMENT_DECREMENT: self.DOWNSTREAM_USER_COMMENT_DECREMENT,
            EventName.DOWNSTREAM_FORUM_POST_DECREMENT: self.DOWNSTREAM_FORUM_POST_DECREMENT,
            EventName.DOWNSTREAM_POST_COMMENT_DECREMENT: self.DOWNSTREAM_POST_COMMENT_DECREMENT,
        }


class UpstreamWorkerConfig(BaseWorkerConfig):
    POST_DELETE: Annotated[int, Field(ge=0, default=0)]
    COMMENT_DELETE: Annotated[int, Field(ge=0, default=0)]
    FORUM_DELETE: Annotated[int, Field(ge=0, default=0)]
    POST_SAVE: Annotated[int, Field(ge=0, default=0)]
    POST_VOTE: Annotated[int, Field(ge=0, default=0)]
    COMMENT_VOTE: Annotated[int, Field(ge=0, default=0)]
    FORUM_SUB: Annotated[int, Field(ge=0, default=0)]
    ANIME_SUB: Annotated[int, Field(ge=0, default=0)]

    @cached_property
    def worker_count_mapping(self) -> dict[EventName, int]:
        return {
            EventName.POST_DELETE: self.POST_DELETE,
            EventName.COMMENT_DELETE: self.COMMENT_DELETE,
            EventName.FORUM_DELETE: self.FORUM_DELETE,
            EventName.POST_SAVE: self.POST_SAVE,
            EventName.POST_VOTE: self.POST_VOTE,
            EventName.COMMENT_VOTE: self.COMMENT_VOTE,
            EventName.FORUM_SUB: self.FORUM_SUB,
            EventName.ANIME_SUB: self.ANIME_SUB,
        }


class WorkerSettings(BaseSettings):
    READER: StreamReaderConfig
    DLQ: DeadLetterWorkerConfig
    COUNTERS: CounterConfig
    UPSTREAM: UpstreamWorkerConfig
    DOWNSTREAM_DELETION: DownstreamDeletionWorkerConfig
    DOWNSTREAM_DECREMENT: DownstreamDecrementWorkerConfig

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

    @model_validator(mode="after")
    def dlq_check(self) -> Self:
        if self.COUNTERS.WORKERS and not self.DLQ.COUNTERS:
            raise ValueError(
                " ".join(
                    (
                        f"Non-zero ({self.COUNTERS.WORKERS}) specified with",
                        "no counter DLQ workers present",
                    )
                )
            )
        elif not (
            self.DLQ.STANDARD
            or all(
                map(
                    lambda x: x.dormant,
                    (
                        self.DOWNSTREAM_DECREMENT,
                        self.DOWNSTREAM_DECREMENT,
                        self.READER,
                        self.UPSTREAM,
                    ),
                )
            )
        ):
            raise ValueError(
                " ".join(
                    (
                        f"Non-zero event-processors specified with no",
                        "standard DLQ consumer",
                    )
                )
            )

        return self

    @model_validator(mode="after")
    def check_consumption_pipeline_integrity(self) -> Self:
        worker_mapping: dict[EventName, int] = (
            self.UPSTREAM.worker_count_mapping
            | self.DOWNSTREAM_DECREMENT.worker_count_mapping
            | self.DOWNSTREAM_DELETION.worker_count_mapping
        )

        unused_workers: defaultdict[StreamName, list[EventName]] = defaultdict(list)
        missing_workers: defaultdict[StreamName, list[EventName]] = defaultdict(list)
        for stream, reader_count in self.READER.reader_count_mapping.items():
            stream_events: tuple[EventName, ...] = STREAM_EVENT_MAPPING[stream]
            if reader_count == 0 and any(
                unused := [i for i in stream_events if worker_mapping[i] != 0]
            ):
                unused_workers[stream].extend(unused)
            if reader_count != 0 and any(
                missing := [i for i in stream_events if worker_mapping[i] == 0]
            ):
                missing_workers[stream].extend(missing)

        err_msgs: list[str] = []
        if unused_workers:
            err_msgs.extend(
                " ".join(
                    (
                        f"No stream readers for stream {stream}",
                        "but workers specified for stream events:",
                        ",".join(i.value for i in events),
                    )
                )
                for stream, events in unused_workers.items()
            )

        if missing_workers:
            err_msgs.extend(
                " ".join(
                    (
                        f"Stream readers specified for stream {stream}",
                        "but no workers specified for stream events:",
                        ",".join(i.value for i in events),
                    )
                )
                for stream, events in missing_workers.items()
            )

        if err_msgs:
            raise ValueError("\n".join(err_msgs))

        return self

    @classmethod
    def update_toml_file(cls, filepath: str) -> None:
        path: Path = Path(filepath)
        if not path.is_file():
            raise FileNotFoundError(filepath)
        if (ext := path.name.split(".")[-1]) != "toml":
            raise ValueError(f"Expected TOML file, got {ext} instead")

        cls.model_config = SettingsConfigDict(toml_file=str(filepath))
