from typing import Annotated
from pydantic import BaseModel, Field
from pydantic.networks import IPvAnyAddress

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


class CoreConfigModel(BaseModel):
    APPLICATION_ROOT: Annotated[str, Field(pattern=utils.APP_ROOT_PATTERN, frozen=True)]

    PORT: Annotated[int, Field(ge=1024, le=65_535, frozen=True)]


class JWKSConfigModel(BaseModel):
    JWKS_FILENAME: Annotated[
        str, Field(pattern=utils.JWKS_NAME_PATTERN, default="jwks.json")
    ]

    JWKS_CAP: Annotated[int, Field(ge=1)]


class KeyConfigModel(BaseModel):
    MAX_VALID_KEYS: Annotated[int, Field(ge=1)]
    KEY_ROTATION_COOLDOWN: Annotated[int, Field(ge=0)]


class AdminConfigModel(BaseModel):
    SUSPICIOUS_LOOKBACK_TIME: Annotated[int, Field(ge=1)]
    MAX_ACTIVITY_LIMIT: Annotated[int, Field(ge=0)]
    MAX_SESSION_ITERATIONS: Annotated[int, Field(ge=1)]
    ADMIN_SESSION_DURATION: Annotated[int, Field(ge=0)]


class SAConfigModel(BaseModel):
    SQLALCHEMY_POOL_SIZE: Annotated[int, Field(ge=1)]
    SQLALCHEMY_MAX_OVERFLOW: Annotated[int, Field(ge=0)]
    SQLALCHEMY_POOL_RECYCLE: Annotated[int, Field(ge=0)]
    SQLALCHEMY_POOL_TIMEOUT: Annotated[int, Field(ge=0)]
    SQLALCHEMY_TRACK_MODIFICATIONS: Annotated[bool, Field(default=False)]


class DatabaseConfigModel(BaseModel):
    POSTGRES_HOST: str | IPvAnyAddress
    PISTGRES_PORT: Annotated[int, Field(ge=1024, le=65_535)]
    POSTGRES_DATABASE: str

    SQLALCHEMY: Annotated[SAConfigModel, Field(alias="sqlalchemy")]


class RedisStoreModel(BaseModel):
    HOST: Annotated[str, Field(min_length=1)]
    PORT: Annotated[int, Field(default=6379, ge=1024, le=65_535)]
    DB: Annotated[int, Field(ge=0)]
    DECODE_RESPONSES: Annotated[bool, Field(default=False)]


class RedisConfigModel(BaseModel):
    SYNCED_STORE: Annotated[RedisStoreModel, Field(alias="synced_store")]
    TOKEN_STORE: Annotated[RedisStoreModel, Field(alias="token_store")]
