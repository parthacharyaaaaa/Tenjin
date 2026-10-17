import re
from functools import cached_property
from pathlib import Path
from typing import Annotated, Any, Self

from auxillary.mixins.annotations import timedelta_s
from auxillary.mixins.db_config import (
    BasicPostgresDatabaseConfigMixin,
    BasicSQLAlchemyConfigMixin,
)
from auxillary.mixins.redis_config import BasicRedisConfigMixin
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    PrivateFormat,
    PublicFormat,
)
from pydantic import (
    BaseModel,
    Field,
    computed_field,
    model_validator,
)
from pydantic.config import ConfigDict

from auth_server.config import utils

__all__ = (
    "CoreConfigModel",
    "JWKSConfigModel",
    "KeyConfigModel",
    "AdminConfigModel",
    "SAConfigModel",
    "DatabaseConfigModel",
    "RedisConfigModel",
)


def _parse_jwks_path(path: str) -> Path:
    if not re.match(utils.JWKS_NAME_PATTERN, path):
        raise ValueError(f"Invalid JWKS filename: {path}")
    return Path(path)


class CoreConfigModel(BaseModel):
    APPLICATION_ROOT: Annotated[str, Field(pattern=utils.APP_ROOT_PATTERN, frozen=True)]
    PORT: Annotated[int, Field(ge=1024, le=65_535, frozen=True)]
    WORKING_DIRECTORY: Annotated[Path, Field(default_factory=Path.cwd)]

    @computed_field
    @cached_property
    def instance_path(self) -> Path:
        return self.WORKING_DIRECTORY / "instance"

    @computed_field
    @cached_property
    def static_path(self) -> Path:
        return self.WORKING_DIRECTORY / "static"


class TokenManagerConfigModel(BaseModel):
    REFRESH_LIFETIME: timedelta_s
    ACCESS_LIFETIME: timedelta_s
    LEEWAY: timedelta_s
    ANNOUNCEMENT_DURATION: timedelta_s
    ALG: Annotated[str, Field(default="ES256")]
    MAX_TOKENS_PER_FAMILY: Annotated[int, Field(ge=1)]
    POLL_INTERVAL: timedelta_s

    @model_validator(mode="after")
    def verify_time_values(self) -> Self:
        if self.ACCESS_LIFETIME > self.REFRESH_LIFETIME:
            raise ValueError(
                " ".join(
                    (
                        f"Access lifetime {self.ACCESS_LIFETIME}",
                        "must be lower than refresh lifetime",
                        str(self.REFRESH_LIFETIME),
                    )
                )
            )

        if self.LEEWAY > self.ACCESS_LIFETIME:
            raise ValueError(
                " ".join(
                    (
                        f"Token leeway {self.LEEWAY}",
                        "must be lower than access lifetime",
                        str(self.ACCESS_LIFETIME),
                    )
                )
            )

        return self

    def to_constructor_kwargs(self) -> dict[str, Any]:
        return {
            "refresh_lifetime": self.REFRESH_LIFETIME,
            "access_lifetime": self.ACCESS_LIFETIME,
            "alg": self.ALG,
            "typ": "jwt",
            "leeway": self.LEEWAY,
            "max_tokens_per_fid": self.MAX_TOKENS_PER_FAMILY,
            "announcement_duration": self.ANNOUNCEMENT_DURATION,
        }


class JWKSConfigModel(BaseModel):
    JWKS_CAP: Annotated[int, Field(ge=1)]
    UPDATION_STREAM_NAME: str
    TOKEN_MANAGER: Annotated[TokenManagerConfigModel, Field(alias="token_manager")]


class KeyConfigModel(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    MAX_VALID_KEYS: Annotated[int, Field(ge=1)]
    KEY_ROTATION_COOLDOWN: timedelta_s
    KEY_IDENTIFIER_LENGTH: Annotated[int, Field(ge=1)]
    SIGNATURE_ALGORITHM: Annotated[str, Field(default="ES256")]
    EC_TYPE: Annotated[ec.EllipticCurve, Field(default_factory=ec.SECP256R1)]
    PRIVATE_PEM_FORMAT: Annotated[PrivateFormat, Field(default=PrivateFormat.PKCS8)]
    PUBLIC_PEM_FORMAT: Annotated[
        PublicFormat, Field(default=PublicFormat.SubjectPublicKeyInfo)
    ]


class AdminConfigModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    SUSPICIOUS_LOOKBACK_TIME: timedelta_s
    MAX_ACTIVITY_LIMIT: Annotated[int, Field(ge=0)]
    MAX_SESSION_ITERATIONS: Annotated[int, Field(ge=1)]
    ADMIN_SESSION_DURATION: timedelta_s
    REVIVAL_DIGEST_LENGTH: Annotated[int, Field(ge=1)]


class SAConfigModel(BasicSQLAlchemyConfigMixin, BaseModel): ...


class DatabaseConfigModel(BasicPostgresDatabaseConfigMixin, BaseModel):
    SQLALCHEMY: Annotated[SAConfigModel, Field(alias="sqlalchemy")]


class RedisStoreModel(BasicRedisConfigMixin, BaseModel): ...


class RedisConfigModel(BaseModel):
    SYNCED_STORE: Annotated[RedisStoreModel, Field(alias="synced_store")]
    TOKEN_STORE: Annotated[RedisStoreModel, Field(alias="token_store")]


class BootupConfigModel(BaseModel):
    SLAVE_SLEEP_POLLING_INTERVAL: timedelta_s
    MASTER_BOOTUP_LOCK_LIFESPAN: timedelta_s
