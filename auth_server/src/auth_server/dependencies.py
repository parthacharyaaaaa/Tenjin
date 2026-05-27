import os
from functools import lru_cache
from typing import Final, Generator

from redis import Redis

from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy import create_engine, Engine

from auth_server.src.auth_server.config import AppConfig
from auth_server.src.auth_server.security.token_manager import TokenManager


@lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    return AppConfig()  # type: ignore[reportCallIssue]


@lru_cache(maxsize=1)
def get_synced_store_client() -> Redis:
    config: Final[AppConfig] = get_app_config()

    return Redis(
        username=os.environ["AUTH_WORKER_REDIS_USERNAME"],
        password=os.environ["AUTH_WORKER_REDIS_PASSWORD"],
        **config.REDIS.SYNCED_STORE.to_constructor_kwargs(),
    )


@lru_cache(maxsize=1)
def get_token_store_client() -> Redis:
    config: Final[AppConfig] = get_app_config()

    return Redis(
        username=os.environ["AUTH_WORKER_REDIS_USERNAME"],
        password=os.environ["AUTH_WORKER_REDIS_PASSWORD"],
        **config.REDIS.TOKEN_STORE.to_constructor_kwargs(),
    )


@lru_cache(maxsize=1)
def get_database_session_maker() -> sessionmaker[Session]:
    config: Final[AppConfig] = get_app_config()

    URI: Final[str] = config.DATABASE.SQLALCHEMY.derive_sqlalchemy_uri(
        username=os.environ["POSTGRES_USERNAME"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=str(config.DATABASE.POSTGRES_HOST),
        port=config.DATABASE.POSTGRES_PORT,
        database=config.DATABASE.POSTGRES_DATABASE,
    )

    engine: Final[Engine] = create_engine(URI)

    session_maker: Final[sessionmaker[Session]] = sessionmaker(
        bind=engine, autocommit=False, autoflush=False
    )

    return session_maker


def get_database_session() -> Generator[Session, None, None]:
    session_maker: sessionmaker = get_database_session_maker()
    session: Final[Session] = session_maker()
    try:
        yield session
    finally:
        session.close()


@lru_cache(maxsize=1)
def get_token_manager() -> TokenManager:
    return TokenManager(
        interface=get_token_store_client(),
        synced_store=get_synced_store_client(),
    )
