from datetime import timedelta
from ipaddress import ip_address
from typing import Annotated, Self

import jwt

from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    IPvAnyAddress,
    PrivateAttr,
    model_validator,
)

from resource_server.config.constants import DOMAIN_REGEX


def _verify_hostname(s: str) -> str | IPvAnyAddress:
    try:
        return ip_address(s)
    except ValueError:
        pass
    if not DOMAIN_REGEX.match(s.strip().lower()):
        raise ValueError(f"Incorrect application/logical name: {s}")
    return s


class CoreConfig(BaseModel):
    APPLICATION_ROOT: Annotated[str, Field(frozen=True)]
    PORT: Annotated[int, Field(frozen=True, ge=1024, le=65_535)]

    AUTH_SERVER_NAME: str


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


class BaseRedisConfig(BaseModel):
    HOST: Annotated[str | IPvAnyAddress, BeforeValidator(_verify_hostname)]
    PORT: Annotated[int, Field(le=65_535, ge=1024)]
    DB: Annotated[int, Field(default=0, ge=0)]


class RedisConfig(BaseModel):
    APP: Annotated[BaseRedisConfig, Field(alias="app")]
    AUTH: Annotated[BaseRedisConfig, Field(alias="auth")]


class CacheConfig(BaseModel):
    TTL_CAP: Annotated[int, Field(ge=0)]
    TTL_PROMOTION: Annotated[int, Field(ge=0)]
    TTL_STRONGEST: Annotated[int, Field(ge=0)]
    TTL_STRONG: Annotated[int, Field(ge=0)]
    TTL_WEAK: Annotated[int, Field(ge=0)]
    TTL_EPHEMERAL: Annotated[int, Field(ge=0)]

    # Fetch locks, for thundering herds
    TTL_FETCH_LOCK: Annotated[int, Field(ge=0)]
    FETCH_WAITING_INITIAL_INTERVAL: Annotated[int, Field(ge=1)]
    FETCH_WAITING_JITTER: Annotated[int, Field(ge=1)]
    FETCH_WAITING_EXPONENT: Annotated[int, Field(ge=2)]
    FETCH_WAITING_MAX_INTERVALS: Annotated[int, Field(ge=1)]
    FETCH_MAX_RETRIES: Annotated[int, Field(ge=0)]

    NF_SENTINEL_KEY: str
    NF_SENTINEL_VALUE: str

    @property
    def NF_MAPPING(self) -> dict[str, str]:
        return {self.NF_SENTINEL_KEY: self.NF_SENTINEL_VALUE}

    @model_validator(mode="after")
    def validate_ttl_times(self) -> Self:
        time_dict: dict[str, int] = {
            "maximum": self.TTL_CAP,
            "strongest": self.TTL_STRONGEST,
            "strong": self.TTL_STRONG,
            "weak": self.TTL_WEAK,
            "ephemeral": self.TTL_EPHEMERAL,
            "promotion": self.TTL_PROMOTION,
        }

        if sorted(time_dict.values(), reverse=True) != list(time_dict.values()):
            print(sorted(time_dict.values()), list(time_dict.values()))
            raise ValueError(
                " ".join(
                    (
                        "Cache TTL Values inconsistent, descending order:",
                        ", ".join(time_dict.keys()),
                        "got:",
                        ", ".join(f"{k}: {v}" for k, v in time_dict.items()),
                    )
                )
            )
        return self

    @model_validator(mode="after")
    def validate_fetch_lock_times(self) -> Self:
        max_waiting_time: float = sum(
            (
                self.FETCH_WAITING_INITIAL_INTERVAL
                * self.FETCH_WAITING_JITTER**self.FETCH_WAITING_EXPONENT
            )
            for _ in range(self.FETCH_WAITING_MAX_INTERVALS)
        )
        if self.FETCH_WAITING_JITTER > self.FETCH_WAITING_INITIAL_INTERVAL:
            raise ValueError(
                " ".join(
                    (
                        f"Jitter ({self.FETCH_WAITING_JITTER})",
                        "cannot be greater than initial waiting",
                        f"time {self.FETCH_WAITING_INITIAL_INTERVAL}",
                    )
                )
            )

        if self.TTL_FETCH_LOCK < max_waiting_time:
            raise ValueError(
                " ".join(
                    (
                        f"Fetch lock lifespan {self.TTL_FETCH_LOCK}",
                        "Must be greater than highest possible",
                        f"waiting time ({max_waiting_time})",
                    )
                )
            )
        return self


class BusinessConfig(BaseModel):
    ACCOUNT_RECOVERY_PERIOD: Annotated[
        timedelta, BeforeValidator(lambda x: timedelta(days=x))
    ]
    PASSWORD_TOKEN_MAX_AGE: Annotated[
        timedelta, BeforeValidator(lambda x: timedelta(minutes=x))
    ]
    ACCOUNT_AUDIT_THRESHOLD: Annotated[
        timedelta, BeforeValidator(lambda x: timedelta(days=x))
    ]


class JWKSConfig(BaseModel):
    JWKS_ENDPOINT: str
    JWKS_REQUEST_TIMEOUT: Annotated[int, Field(ge=1)]
    JWKS_POLL_INTERVAL: Annotated[int, Field(ge=1)]
    UPDATION_LOCK_LIFESPAN: Annotated[int, Field(ge=1)]

    KEY_ANNOUNCEMENT_DURATION: Annotated[int, Field(ge=0)]
    MAX_GLOBAL_MAPPING_POLLS: Annotated[int, Field(ge=1)]
    GLOBAL_MAPPING_POLL_INTERVAL: Annotated[int, Field(ge=0)]
    SLAVE_WAIT_INTERVAL: Annotated[int, Field(ge=0)]

    KEY_LEEWAY: Annotated[int, Field(ge=0)]
    ALLOWED_ALGORITHMS: Annotated[
        frozenset[str], BeforeValidator(lambda x: frozenset(i.upper() for i in x))
    ]

    KEY_ANNOUNCEMENT_AUTH_CHANNEL: str

    # TODO: Add validation for time values
    @model_validator(mode="after")
    def validate_algorithms(self) -> Self:
        if unsupported_algs := self.ALLOWED_ALGORITHMS - set(
            jwt.PyJWS().get_algorithms()
        ):
            raise ValueError(f"Unsupported algorithms: {', '.join(unsupported_algs)}")
        return self
