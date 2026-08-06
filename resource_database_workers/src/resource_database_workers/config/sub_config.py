from ipaddress import ip_address
from typing import Annotated

from auxillary.mixins.db_config import (
    BasicConnectionPoolConfigMixin,
    BasicPostgresDatabaseConfigMixin,
)
from auxillary.mixins.redis_config import BasicRedisConfigMixin
from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    IPvAnyAddress,
)

from resource_auxillary import config_mixins

from resource_database_workers.config.constants import DOMAIN_REGEX


def _verify_hostname(s: str) -> str | IPvAnyAddress:
    try:
        return ip_address(s)
    except ValueError:
        pass
    if not DOMAIN_REGEX.match(s.strip().lower()):
        raise ValueError(f"Incorrect application/logical name: {s}")
    return s


class RedisConfig(BasicRedisConfigMixin, BaseModel): ...


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
    COUNTER_REGISTRY_REFRESH_INTERVAL: Annotated[int, Field(ge=0)]
    COUNTER_FLUSH_LOCK_TTL: Annotated[int, Field(ge=0)]
    COUNTER_FLUSH_INTERVAL: Annotated[int, Field(ge=0)]

    # Downstream counter consumers
    DOWNSTREAM_COUNTER_BATCH_SIZE: Annotated[int, Field(ge=1)]

    # Others
    GRACEFUL_SHUTDOWN_PERIOD: Annotated[float, Field(ge=0)]


class DatabaseConfig(
    BasicPostgresDatabaseConfigMixin, BasicConnectionPoolConfigMixin, BaseModel
): ...
