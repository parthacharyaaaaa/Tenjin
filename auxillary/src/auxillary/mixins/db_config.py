import multiprocessing
from typing import Annotated, ClassVar, Self

from pydantic import Field, model_validator


class BasicSQLAlchemyConfigMixin:
    SQLALCHEMY_DATABASE_URI_TEMPLATE: ClassVar[str] = (
        "postgresql+psycopg://{username}:{password}@{host}:{port}/{database}"
    )

    SQLALCHEMY_POOL_SIZE: Annotated[int, Field(ge=1)]
    SQLALCHEMY_MAX_OVERFLOW: Annotated[int, Field(ge=0)]
    SQLALCHEMY_POOL_RECYCLE: Annotated[int, Field(ge=1)]
    SQLALCHEMY_POOL_TIMEOUT: Annotated[int, Field(ge=1)]
    SQLALCHEMY_TRACK_MODIFICATIONS: Annotated[bool, Field(default=False)]

    @classmethod
    def construct_sqlalchemy_uri(
        cls, username: str, password: str, host: str, port: int, database: str
    ) -> str:
        return cls.SQLALCHEMY_DATABASE_URI_TEMPLATE.format(
            username=username,
            password=password,
            host=host,
            port=port,
            database=database,
        )


class BasicConnectionPoolConfigMixin:
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


class BasicPostgresDatabaseConfigMixin:
    DATABASE_URI_TEMPLATE: ClassVar[str] = (
        "postgresql+psycopg2://{username}:{password}@{host}:{port}/{database}"
    )

    POSTGRES_HOST: str
    POSTGRES_PORT: Annotated[int, Field(ge=1024, le=65_535)]
    POSTGRES_DATABASE: str

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
