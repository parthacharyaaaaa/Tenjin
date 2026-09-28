import os
import sys
from pathlib import Path
from typing import Final

from auth_server.config.app_config import AppConfig
from auth_server.dependencies.local import get_app_config
from auth_server.models.database import Admin, KeyData, SuspiciousActivity
from dotenv import load_dotenv
from psycopg import connect
from psycopg.conninfo import make_conninfo
from psycopg.sql import SQL, Composed, Identifier, Literal


def ensure_user(conn, username: str, password: str) -> None:
    exists = conn.execute(
        "SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = %s",
        (username,),
    ).fetchone()

    action = SQL("ALTER USER") if exists else SQL("CREATE USER")
    conn.execute(
        SQL("{} {} WITH PASSWORD {}").format(
            action,
            Identifier(username),
            Literal(password),
        )
    )


AUTH_SERVER_DCL_TEMPLATE: Final[SQL] = SQL(
    """
    GRANT CONNECT ON DATABASE {auth_database_name}
    TO {auth_server_username};

    GRANT SELECT, DELETE, UPDATE, INSERT ON {admins_table}, {sus_table}, {keys_table}
    TO {auth_server_username};
    """
)


def main(env_filepath: str | None = None) -> None:
    env_filepath = env_filepath or os.path.join(
        Path(__file__).parent.parent.parent.parent, ".env"
    )
    if not load_dotenv(env_filepath):
        raise FileNotFoundError("Failed to load env file")
    app_config: Final[AppConfig] = get_app_config()
    conninfo: Final[str] = make_conninfo(
        user=os.environ["SUPERUSER_POSTGRES_USERNAME"],
        password=os.environ["SUPERUSER_POSTGRES_PASSWORD"],
        host=app_config.DATABASE.POSTGRES_HOST,
        port=app_config.DATABASE.POSTGRES_PORT,
        dbname=app_config.DATABASE.POSTGRES_DATABASE,
    )

    auth_server_composed_stmt: Final[Composed] = AUTH_SERVER_DCL_TEMPLATE.format(
        auth_server_username=Identifier(os.environ["AUTH_SERVER_POSTGRES_USERNAME"]),
        auth_database_name=Identifier(app_config.DATABASE.POSTGRES_DATABASE),
        admins_table=Identifier(Admin.__tablename__),
        keys_table=Identifier(KeyData.__tablename__),
        sus_table=Identifier(SuspiciousActivity.__tablename__),
    )

    with connect(conninfo) as conn:
        ensure_user(
            conn,
            os.environ["AUTH_SERVER_POSTGRES_USERNAME"],
            os.environ["AUTH_SERVER_POSTGRES_PASSWORD"],
        )
        conn.execute(auth_server_composed_stmt)
        conn.commit()


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
