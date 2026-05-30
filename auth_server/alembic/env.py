from logging.config import fileConfig
import os
from pathlib import Path

from auth_server.config.app_config import AppConfig
from dotenv import load_dotenv
from sqlalchemy import engine_from_config
from sqlalchemy import pool, MetaData

from auth_server.dependencies import get_app_config

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
from auth_server.models.database import Base

target_metadata: MetaData = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def _set_sqlalchemy_uri_config() -> None:
    env_path: Path = Path(__file__).parent.parent / ".env"
    loaded: bool = load_dotenv(str(env_path))
    if not loaded:
        raise FileNotFoundError(f"No env file found at: {env_path}")

    app_config: AppConfig = get_app_config()

    URI: str = app_config.DATABASE.SQLALCHEMY.derive_sqlalchemy_uri(
        username=os.environ["AUTH_WORKER_POSTGRES_USERNAME"],
        password=os.environ["AUTH_WORKER_POSTGRES_PASSWORD"],
        host=str(app_config.DATABASE.POSTGRES_HOST),
        port=app_config.DATABASE.POSTGRES_PORT,
        database=app_config.DATABASE.POSTGRES_DATABASE,
    )

    config.set_main_option("sqlalchemy.url", URI)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    _set_sqlalchemy_uri_config()
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    _set_sqlalchemy_uri_config()
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
