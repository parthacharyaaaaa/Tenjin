from pathlib import Path
from typing import Annotated, Any
from functools import cached_property
from pydantic import BaseModel, BeforeValidator, Field, PrivateAttr, computed_field
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

    WORKING_DIRECTORY: Annotated[Path, Field(default_factory=Path.cwd)]

    @computed_field
    @cached_property
    def instance_path(self) -> Path:
        return self.WORKING_DIRECTORY / "instance"

    @computed_field
    @cached_property
    def static_path(self) -> Path:
        return self.WORKING_DIRECTORY / "static"


class JWKSConfigModel(BaseModel):
    JWKS_FILEPATH: Annotated[
        Path,
        BeforeValidator(lambda d: Path(d)),
        Field(
            pattern=utils.JWKS_NAME_PATTERN, default="jwks.json", alias="JWKS_FILENAME"
        ),
    ]

    PUBLIC_PEM_DIRECTORY: Annotated[Path, BeforeValidator(lambda d: Path(d))]
    PRIVATE_PEM_DIRECTORY: Annotated[Path, BeforeValidator(lambda d: Path(d))]

    JWKS_CAP: Annotated[int, Field(ge=1)]

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


class SAConfigModel(BaseModel):
    _SQLALCHEMY_DATABASE_URI: str = PrivateAttr(
        "postgresql+psycopg2://{username}:{password}@{host}:{port}/{database}"
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
            username=username, password=password, host=host, port=port, databse=database
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
