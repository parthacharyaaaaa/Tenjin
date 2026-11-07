import os
import sys
from pathlib import Path
from typing import Final

from auth_server.admin.roles import AdminRole
from auth_server.config.app_config import AppConfig
from auth_server.dependencies.local import get_app_config
from auth_server.models.database import Admin
from auxillary.security.hashing import bcrypt_hash_password
from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm.session import sessionmaker
from sqlalchemy.sql.expression import delete, insert, text


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
    with sessionmaker(bind=engine)() as session:
        session.execute(delete(Admin))
        session.execute(
            insert(Admin).values(
                id_=1,
                username=os.environ["GENESIS_SUPERUSER_USERNAME"],
                role=AdminRole.SUPER,
                password_hash=bcrypt_hash_password(
                    os.environ["GENESIS_SUPERUSER_PASSWORD"]
                ),
                created_by=1,
            )
        )
        session.execute(
            text("""
                SELECT setval(
                    pg_get_serial_sequence('admins', 'id_'),
                    COALESCE((SELECT MAX(id_) FROM admins), 1),
                    EXISTS (SELECT 1 FROM admins)
                )
            """)
        )
        session.commit()


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
