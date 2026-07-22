from ipaddress import ip_address
import multiprocessing
from typing import Annotated, ClassVar, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    IPvAnyAddress,
    PrivateAttr,
    model_validator,
)

from resource_database_workers.config.constants import DOMAIN_REGEX


def _verify_hostname(s: str) -> str | IPvAnyAddress:
    try:
        return ip_address(s)
    except ValueError:
        pass
    if not DOMAIN_REGEX.match(s.strip().lower()):
        raise ValueError(f"Incorrect application/logical name: {s}")
    return s


class RedisConfig(BaseModel):
    HOST: Annotated[str | IPvAnyAddress, BeforeValidator(_verify_hostname)]
    PORT: Annotated[int, Field(le=65_535, ge=1024)]
    DB: Annotated[int, Field(default=0, ge=0)]


class RedisContainer(BaseModel):
    APP: Annotated[RedisConfig, Field(alias="app")]
    INTERNAL: Annotated[RedisConfig, Field(alias="internal")]


class WorkerConfig(BaseModel):
    MAX_RETRIES: Annotated[int, Field(ge=0)]
    DLQ_NAME: Annotated[str, BeforeValidator(lambda x: x.strip())]

    # Counters
    COUNTER_REGISTRY_NAME: Annotated[str, BeforeValidator(lambda x: x.strip())]
    COUNTER_RETRY_REGISTRY_NAME: Annotated[str, BeforeValidator(lambda x: x.strip())]
    COUNTER_REGISTRY_REFRESH_INTERVAL: Annotated[int, Field(ge=0)]
    COUNTER_FLUSH_LOCK_TTL: Annotated[int, Field(ge=0)]
    COUNTER_FLUSH_INTERVAL: Annotated[int, Field(ge=0)]

    # Consumers
    CONSUMER_READ_INTERVAL: Annotated[int, Field(ge=0)]
    CONSUMER_READ_SIZE: Annotated[int, Field(ge=1)]
    CONSUMER_BLOCK_TIME: Annotated[int, Field(ge=0)]
    CONSUMER_GROUP_NAME: Annotated[str, Field(frozen=True)]

    # Internal queue consumers
    IQ_CONSUMER_BASE_WAITING_TIME: Annotated[int, Field(ge=0)]
    IQ_CONSUMER_GET_TIMEOUT: Annotated[int, Field(ge=0)]
    IQ_CONSUMER_BATCH_SIZE_QUOTA: Annotated[int, Field(ge=1)]
    IQ_CONSUMER_SLEEP_INTERVAL: Annotated[int, Field(ge=0)]

    # Downstream counter consumers
    DOWNSTREAM_COUNTER_BATCH_SIZE: Annotated[int, Field(ge=1)]

    # Reclaimation
    RECLAIM_THRESHOLD: Annotated[int, Field(ge=1)]
    RECLAIMATION_CHECK_INTERVAL: Annotated[int, Field(ge=1)]
    MAX_DELIVERIES: Annotated[int, Field(ge=1)]

    # Retry backoffs
    MAXIMUM_BACKOFF_INTERVAL: Annotated[int, Field(ge=0)]
    BASE_BACKOFF_INTERVAL: Annotated[int, Field(ge=0)]
    BACKOFF_EXPONENTIAL: Annotated[int, Field(ge=1)]

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

    @model_validator(mode="after")
    def validate_reclamation_interval(self) -> Self:
        if self.RECLAIMATION_CHECK_INTERVAL > self.RECLAIM_THRESHOLD:
            raise ValueError(
                " ".join(
                    (
                        f"Reclaim threshold {self.RECLAIM_THRESHOLD}",
                        "cannot be lesser than reclaimation check interval",
                        str(self.RECLAIMATION_CHECK_INTERVAL),
                    )
                )
            )
        return self


class DatabaseConfig(BaseModel):
    DATABASE_URI_TEMPLATE: ClassVar[str] = (
        "postgresql+psycopg2://{username}:{password}@{host}:{port}/{database}"
    )

    POSTGRES_HOST: Annotated[str | IPvAnyAddress, BeforeValidator(_verify_hostname)]
    POSTGRES_PORT: Annotated[int, Field(ge=1024, le=65_535)]
    POSTGRES_DATABASE: str

    # Defaults field values reflect the default constructor values from psycopg3
    # See: https://www.psycopg.org/psycopg3/docs/api/pool.html#the-connectionpool-class
    CONNECTION_POOL_MIN_SIZE: Annotated[
        int, Field(ge=1, default_factory=multiprocessing.cpu_count)
    ]
    CONNECTION_POOL_MAX_SIZE: Annotated[
        int, Field(ge=1, default_factory=lambda: multiprocessing.cpu_count() * 2)
    ]
    CONNECTION_TIMEOUT: Annotated[int, Field(ge=1, default=30)]
    CONNECTION_MAX_LIFETIME: Annotated[int, Field(ge=1, default=60 * 60)]
    CONNECTION_MAX_IDLE: Annotated[int, Field(ge=1, default=60 * 10)]
    RECONNECT_TIMEOUT: Annotated[int, Field(ge=1, default=60 * 5)]
    NUM_WORKERS: Annotated[int, Field(ge=1, default=3)]

    @classmethod
    def construct_sqlalchemy_uri(
        cls, username: str, password: str, host: str, port: int, database: str
    ) -> str:
        return cls.DATABASE_URI_TEMPLATE.format(
            username=username,
            password=password,
            host=host,
            port=port,
            database=database,
        )

    def derive_sqlalchemy_uri(self, username: str, password: str) -> str:
        return self.construct_sqlalchemy_uri(
            username=username,
            password=password,
            host=str(self.POSTGRES_HOST),
            port=self.POSTGRES_PORT,
            database=self.POSTGRES_DATABASE,
        )

    @model_validator(mode="after")
    def check_connection_pool_sizing(self) -> Self:
        if self.CONNECTION_POOL_MAX_SIZE < self.CONNECTION_POOL_MIN_SIZE:
            raise ValueError(
                " ".join(
                    (
                        "Connection pool min size",
                        str(self.CONNECTION_POOL_MIN_SIZE),
                        "cannot be greater than max size",
                        str(self.CONNECTION_POOL_MAX_SIZE),
                    )
                )
            )
        return self

    def emit_connection_pool_constructor_kwargs(self) -> dict[str, int]:
        return {
            "min_size": self.CONNECTION_POOL_MIN_SIZE,
            "max_size": self.CONNECTION_POOL_MAX_SIZE,
            "timeout": self.CONNECTION_TIMEOUT,
            "max_lifetime": self.CONNECTION_MAX_LIFETIME,
            "reconnect_timeout": self.RECONNECT_TIMEOUT,
            "max_idle": self.CONNECTION_MAX_IDLE,
            "num_workers": self.NUM_WORKERS,
        }
