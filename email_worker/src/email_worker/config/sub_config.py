from typing import Annotated

from auxillary.mixins.annotations import timedelta_s
from auxillary.mixins.db_config import (
    BasicConnectionPoolConfigMixin,
    BasicPostgresDatabaseConfigMixin,
)
from pydantic import BaseModel, BeforeValidator, Field

from resource_auxillary import config_mixins


class DatabaseConfig(
    BasicPostgresDatabaseConfigMixin, BasicConnectionPoolConfigMixin, BaseModel
):
    pass


class EmailWorkerConfig(
    config_mixins.WorkerStreamReaderMixin,
    config_mixins.WorkerInternalQueueMixin,
    config_mixins.WorkerRetryMixin,
    config_mixins.WorkerReclaimMixin,
    config_mixins.WorkerDLQMixin,
    BaseModel,
):
    GRACEFUL_SHUTDOWN_PERIOD: timedelta_s
    SMTP_NETWORK_ERROR_WINDOW: timedelta_s
    MAXIMUM_SMTP_REFRESHES: Annotated[int, Field(ge=0)]
    WORKER_TASK_PREFIX: Annotated[str, BeforeValidator(lambda x: x.strip())]


class EmailConfig(BaseModel):
    hostname: str
    port: Annotated[int, Field(ge=1024, le=65_535)]
    use_tls: Annotated[bool, Field(default=True)]
    WORKER: Annotated[EmailWorkerConfig, Field(alias="worker")]


class RedisConfig(BaseModel):
    HOSTNAME: str
    PORT: Annotated[int, Field(ge=1024, le=65_535)]
    DB: Annotated[int, Field(ge=0)]
    DECODE_RESPONSES: Annotated[bool, Field(default=True)]
