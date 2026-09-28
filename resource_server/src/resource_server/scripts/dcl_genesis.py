import os
import sys
from pathlib import Path
from typing import Final

from dotenv import load_dotenv
from psycopg import connect
from psycopg.conninfo import make_conninfo
from psycopg.sql import SQL, Composed, Identifier, Literal
from resource_server.config.app_config import AppConfig
from resource_server.dependencies import get_app_config
from resource_server.models.database import (
    Anime,
    Comment,
    CommentReport,
    CommentVote,
    Forum,
    ForumAdmin,
    PasswordRecoveryToken,
    Post,
    PostReport,
    PostSave,
    PostVote,
    User,
)


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


RESOURCE_SERVER_DCL_TEMPLATE: Final[SQL] = SQL(
    """
    GRANT CONNECT ON DATABASE {resource_database_name}
    TO {resource_server_username};

    ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO {resource_server_username};

    GRANT SELECT ON ALL TABLES IN SCHEMA public
    TO {resource_server_username};

    GRANT DELETE ON {users_table}, {password_recovery_tokens_table}
    TO {resource_server_username};

    GRANT INSERT ON {forums_table}, {forum_admins_table}, {users_table}, {password_recovery_tokens_table}
    TO {resource_server_username};

    GRANT UPDATE (name_, description) ON {forums_table} TO {resource_server_username};
    GRANT UPDATE (role) ON {forum_admins_table} TO {resource_server_username};
    GRANT UPDATE (closed, body_text, title) ON {posts_table} TO {resource_server_username};
    GRANT UPDATE (pw_hash, last_login) ON {users_table} TO {resource_server_username};
    """
)

ASYNC_WORKER_DCL_TEMPLATE: Final[SQL] = SQL(
    """
    GRANT CONNECT ON DATABASE {resource_database_name}
    TO {async_worker_username};

    GRANT SELECT ON ALL TABLES IN SCHEMA public
    TO {async_worker_username};

    GRANT SELECT ON ALL TABLES IN SCHEMA PUBLIC TO {async_worker_username};
    GRANT UPDATE ON ALL TABLES IN SCHEMA PUBLIC TO {async_worker_username};
    GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {async_worker_username};

    GRANT DELETE ON {posts_table}, {comments_table}, {forums_table},
    {post_votes_table}, {post_saves_table}, {post_reports_table},
    {comment_votes_table}, {comment_reports_table}
    TO {async_worker_username};

    GRANT INSERT ON {posts_table}, {comments_table}, {forums_table},
    {post_votes_table}, {post_saves_table}, {post_reports_table},
    {comment_votes_table}, {comment_reports_table}
    TO {async_worker_username};
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

    resource_server_composed_stmt: Final[Composed] = (
        RESOURCE_SERVER_DCL_TEMPLATE.format(
            resource_server_username=Identifier(
                os.environ["RESOURCE_SERVER_POSTGRES_USERNAME"]
            ),
            resource_database_name=Identifier(app_config.DATABASE.POSTGRES_DATABASE),
            users_table=Identifier(User.__tablename__),
            password_recovery_tokens_table=Identifier(
                PasswordRecoveryToken.__tablename__
            ),
            forums_table=Identifier(Forum.__tablename__),
            forum_admins_table=Identifier(ForumAdmin.__tablename__),
            posts_table=Identifier(Post.__tablename__),
        )
    )

    async_worker_composed_stmt: Final[Composed] = ASYNC_WORKER_DCL_TEMPLATE.format(
        async_worker_username=Identifier(os.environ["WORKER_POSTGRES_USERNAME"]),
        resource_database_name=Identifier(app_config.DATABASE.POSTGRES_DATABASE),
        posts_table=Identifier(Post.__tablename__),
        comments_table=Identifier(Comment.__tablename__),
        forums_table=Identifier(Forum.__tablename__),
        post_votes_table=Identifier(PostVote.__tablename__),
        post_saves_table=Identifier(PostSave.__tablename__),
        post_reports_table=Identifier(PostReport.__tablename__),
        comment_votes_table=Identifier(CommentVote.__tablename__),
        comment_reports_table=Identifier(CommentReport.__tablename__),
        animes_table=Identifier(Anime.__tablename__),
        users_table=Identifier(User.__tablename__),
    )

    with connect(conninfo) as conn:
        ensure_user(
            conn,
            os.environ["RESOURCE_SERVER_POSTGRES_USERNAME"],
            os.environ["RESOURCE_SERVER_POSTGRES_PASSWORD"],
        )
        ensure_user(
            conn,
            os.environ["WORKER_POSTGRES_USERNAME"],
            os.environ["WORKER_POSTGRES_PASSWORD"],
        )
        conn.execute(resource_server_composed_stmt)
        conn.execute(async_worker_composed_stmt)
        conn.commit()


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
