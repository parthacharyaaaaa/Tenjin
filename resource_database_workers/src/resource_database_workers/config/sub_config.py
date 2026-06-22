from ipaddress import ip_address
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field, IPvAnyAddress, PrivateAttr

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


class SQLAlchemyConfig(BaseModel):
    _SQLALCHEMY_DATABASE_URI_TEMPLATE: str = PrivateAttr(
        default="postgresql+psycopg2://{username}:{password}@{host}:{port}/{database}"
    )
    SQLALCHEMY_POOL_SIZE: Annotated[int, Field(ge=1)]
    SQLALCHEMY_MAX_OVERFLOW: Annotated[int, Field(ge=0)]
    SQLALCHEMY_POOL_RECYCLE: Annotated[int, Field(ge=1)]
    SQLALCHEMY_POOL_TIMEOUT: Annotated[int, Field(ge=1)]
    SQLALCHEMY_TRACK_MODIFICATIONS: Annotated[bool, Field(default=False)]

    def derive_sqlalchemy_uri(
        self, username: str, password: str, host: str, port: int, database: str
    ) -> str:
        return self._SQLALCHEMY_DATABASE_URI_TEMPLATE.format(
            username=username,
            password=password,
            host=host,
            port=port,
            database=database,
        )


class DatabaseConfig(BaseModel):
    POSTGRES_HOST: Annotated[str | IPvAnyAddress, BeforeValidator(_verify_hostname)]
    POSTGRES_PORT: Annotated[int, Field(ge=1024, le=65_535)]
    POSTGRES_DATABASE: str

    SQLALCHEMY: SQLAlchemyConfig
