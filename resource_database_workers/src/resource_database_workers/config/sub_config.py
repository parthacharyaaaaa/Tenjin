from typing import Annotated

from auxillary.mixins.annotations import timedelta_s
from auxillary.mixins.cache_config import BasicCacheTTLConfig, BasicNegativeCacheConfig
from auxillary.mixins.db_config import (
    BasicConnectionPoolConfigMixin,
    BasicPostgresDatabaseConfigMixin,
)
from auxillary.mixins.redis_config import BasicRedisConfigMixin
from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
)

from resource_auxillary import config_mixins


class RedisConfig(BasicRedisConfigMixin, BaseModel): ...


class CacheConfig(BasicCacheTTLConfig, BasicNegativeCacheConfig, BaseModel): ...


class RedisContainer(BaseModel):
    APP: Annotated[RedisConfig, Field(alias="app")]
    INTERNAL: Annotated[RedisConfig, Field(alias="internal")]


class WorkerConfig(
    config_mixins.WorkerStreamReaderMixin,
    config_mixins.WorkerInternalQueueMixin,
    config_mixins.WorkerRetryMixin,
    config_mixins.WorkerReclaimMixin,
    config_mixins.WorkerDLQMixin,
    BaseModel,
):
    # Counters
    COUNTER_REGISTRY_NAME: Annotated[str, BeforeValidator(lambda x: x.strip())]
    COUNTER_RETRY_REGISTRY_NAME: Annotated[str, BeforeValidator(lambda x: x.strip())]
    COUNTER_REGISTRY_REFRESH_INTERVAL: timedelta_s
    COUNTER_FLUSH_LOCK_TTL: timedelta_s
    COUNTER_FLUSH_INTERVAL: timedelta_s

    # Downstream
    PROCESSING_CHECKPOINT_PREFIX: Annotated[
        str, Field(frozen=True, default="_CHECKPOINT")
    ]

    # Downstream counter consumers
    DOWNSTREAM_COUNTER_BATCH_SIZE: Annotated[int, Field(ge=1)]
    DOWNSTREAM_CACHE_INVALIDATION_BATCH_SIZE: Annotated[int, Field(ge=1)]

    # Others
    GRACEFUL_SHUTDOWN_PERIOD: timedelta_s

    COUNTER_WORKER_TASK_PREFIX: Annotated[str, BeforeValidator(lambda x: x.strip())]
    RETRY_COUNTER_WORKER_TASK_PREFIX: Annotated[
        str, BeforeValidator(lambda x: x.strip())
    ]
    STREAM_READER_TASK_PREFIX: Annotated[str, BeforeValidator(lambda x: x.strip())]
    STREAM_WORKER_TASK_PREFIX: Annotated[str, BeforeValidator(lambda x: x.strip())]


class DatabaseConfig(
    BasicPostgresDatabaseConfigMixin, BasicConnectionPoolConfigMixin, BaseModel
): ...
