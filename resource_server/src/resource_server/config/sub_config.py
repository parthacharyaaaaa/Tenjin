from datetime import timedelta
from ipaddress import ip_address
from typing import Annotated, Self

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


class RedisConfig(BaseModel):
    HOST: Annotated[str | IPvAnyAddress, BeforeValidator(_verify_hostname)]
    PORT: Annotated[int, Field(le=65_535, ge=1024)]
    DB: Annotated[int, Field(default=0, ge=0)]


class CacheConfig(BaseModel):
    TTL_CAP: Annotated[int, Field(ge=0)]
    TTL_PROMOTION: Annotated[int, Field(ge=0)]
    TTL_STRONGEST: Annotated[int, Field(ge=0)]
    TTL_STRONG: Annotated[int, Field(ge=0)]
    TTL_WEAK: Annotated[int, Field(ge=0)]
    TTL_EPHEMERAL: Annotated[int, Field(ge=0)]

    NF_SENTINEL_KEY: str
    NF_SENTINEL_VALUE: str

    RESOURCE_CREATION_PENDING_FLAG: str
    RESOURCE_DELETION_PENDING_FLAG: str
    RESOURCE_CREATION_PENDING_ALT_FLAG: str

    KEY_ANNOUNCEMENT_DURATION: Annotated[int, Field(ge=0)]
    JWKS_POLL_COOLDOWN: Annotated[int, Field(ge=0)]

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
    def validate_flag_uniqueness(self) -> Self:
        flags: list[str] = [
            self.RESOURCE_CREATION_PENDING_FLAG,
            self.RESOURCE_CREATION_PENDING_FLAG,
            self.RESOURCE_CREATION_PENDING_ALT_FLAG,
        ]
        if residue := [i for i in flags if i not in set(flags)]:
            raise ValueError(f"Got duplicate flag names: {', '.join(residue)}")
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
