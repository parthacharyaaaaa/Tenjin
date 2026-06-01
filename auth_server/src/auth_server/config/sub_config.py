import hashlib
from pathlib import Path
import re
from typing import Annotated, Any, Callable, Self
from functools import cached_property
from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    PrivateAttr,
    computed_field,
    AfterValidator,
    model_validator,
)
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
    REFRESH_LIFETIME: Annotated[int, Field(ge=1)]
    ACCESS_LIFETIME: Annotated[int, Field(ge=1)]
    LEEWAY: Annotated[int, Field(ge=0)]
    ANNOUNCEMENT_DURATION: Annotated[int, Field(ge=1)]

    ALG: Annotated[str, Field(default="ES256")]

    MAX_TOKENS_PER_FAMILY: Annotated[int, Field(ge=1)]

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

    def to_constructor_kwargs(self) -> dict[str, int | str]:
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
    JWKS_FILEPATH: Annotated[
        Path,
        BeforeValidator(_parse_jwks_path),
        Field(default="jwks.json", alias="JWKS_FILENAME"),
    ]

    PUBLIC_PEM_DIRECTORY: Annotated[
        Path,
        BeforeValidator(lambda d: Path(d)),
        Field(alias="PUBLIC_PEM_BASE_DIRECTORY"),
    ]
    PRIVATE_PEM_DIRECTORY: Annotated[
        Path,
        BeforeValidator(lambda d: Path(d)),
        Field(alias="PRIVATE_PEM_BASE_DIRECTORY"),
    ]

    JWKS_CAP: Annotated[int, Field(ge=1)]

    TOKEN_MANAGER: Annotated[TokenManagerConfigModel, Field(alias="token_manager")]

    def _resolve_path_attr(self, attr_name: str, rootpath: Path) -> None:
        path: Path = rootpath / getattr(self, attr_name)

        if not path.is_absolute():
            raise ValueError(f"Resolved path {path} is not absolute")
        if not path.exists():
            path.mkdir()

        setattr(self, attr_name, path)

    def resolve_public_pem_directory(self, rootpath: Path) -> None:
        self._resolve_path_attr("PUBLIC_PEM_DIRECTORY", rootpath)

    def resolve_private_pem_directory(self, rootpath: Path) -> None:
        self._resolve_path_attr("PRIVATE_PEM_DIRECTORY", rootpath)

    def resolve_jwks_directory(self, rootpath: Path) -> None:
        self._resolve_path_attr("JWKS_FILEPATH", rootpath)


class KeyConfigModel(BaseModel):
    MAX_VALID_KEYS: Annotated[int, Field(ge=1)]
    KEY_ROTATION_COOLDOWN: Annotated[int, Field(ge=0)]


class AdminConfigModel(BaseModel):
    SUSPICIOUS_LOOKBACK_TIME: Annotated[int, Field(ge=1)]
    MAX_ACTIVITY_LIMIT: Annotated[int, Field(ge=0)]
    MAX_SESSION_ITERATIONS: Annotated[int, Field(ge=1)]
    ADMIN_SESSION_DURATION: Annotated[int, Field(ge=0)]
    SESSION_HASHFUNC: Annotated[Callable, Field(default=hashlib.sha256)]


class SAConfigModel(BaseModel):
    _SQLALCHEMY_DATABASE_URI: str = PrivateAttr(
        "postgresql+psycopg://{username}:{password}@{host}:{port}/{database}"
    )
    SQLALCHEMY_POOL_SIZE: Annotated[int, Field(ge=1)]
    SQLALCHEMY_MAX_OVERFLOW: Annotated[int, Field(ge=0)]
    SQLALCHEMY_POOL_RECYCLE: Annotated[int, Field(ge=0)]
    SQLALCHEMY_POOL_TIMEOUT: Annotated[int, Field(ge=0)]
    SQLALCHEMY_TRACK_MODIFICATIONS: Annotated[bool, Field(default=False)]

    def derive_sqlalchemy_uri(
        self, username: str, password: str, host: str, port: int, database: str
    ) -> str:
        return self._SQLALCHEMY_DATABASE_URI.format(
            username=username,
            password=password,
            host=host,
            port=port,
            database=database,
        )


class DatabaseConfigModel(BaseModel):
    POSTGRES_HOST: str | IPvAnyAddress
    POSTGRES_PORT: Annotated[int, Field(ge=1024, le=65_535)]
    POSTGRES_DATABASE: str

    SQLALCHEMY: Annotated[SAConfigModel, Field(alias="sqlalchemy")]


class RedisStoreModel(BaseModel):
    HOST: Annotated[str, Field(min_length=1)]
    PORT: Annotated[int, Field(default=6379, ge=1024, le=65_535)]
    DB: Annotated[int, Field(ge=0)]
    DECODE_RESPONSES: Annotated[bool, Field(default=False)]

    def to_constructor_kwargs(self) -> dict[str, Any]:
        return {
            "host": self.HOST,
            "port": self.PORT,
            "db": self.DB,
            "decode_responses": self.DECODE_RESPONSES,
        }


class RedisConfigModel(BaseModel):
    SYNCED_STORE: Annotated[RedisStoreModel, Field(alias="synced_store")]
    TOKEN_STORE: Annotated[RedisStoreModel, Field(alias="token_store")]
