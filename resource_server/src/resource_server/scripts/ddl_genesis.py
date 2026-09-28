import os
import sys
from pathlib import Path
from typing import Final

from dotenv import load_dotenv
from resource_server.config.app_config import AppConfig
from resource_server.depedencies.local import get_app_config
from resource_server.models.database import Base
from sqlalchemy import Engine, create_engine


def main(env_filepath: str | None = None) -> None:
    env_filepath = env_filepath or os.path.join(
        Path(__file__).parent.parent.parent.parent, ".env"
    )
    if not load_dotenv(env_filepath):
        raise FileNotFoundError("Failed to load env file")
    config: Final[AppConfig] = get_app_config()

    uri: Final[str] = config.DATABASE.derive_sqlalchemy_uri(
        username=os.environ["SUPERUSER_POSTGRES_USERNAME"],
        password=os.environ["SUPERUSER_POSTGRES_PASSWORD"],
    )

    engine: Final[Engine] = create_engine(uri)

    Base.metadata.create_all(bind=engine, checkfirst=True)


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
