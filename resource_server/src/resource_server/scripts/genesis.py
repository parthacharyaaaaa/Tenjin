"""### Populates the database

#### Tables populated:

- Animes
- Forums (1 per anime)
- Genres
- Anime genres
- stream_links
- forum_admins (`owner` as **Tenjin superuser**)
"""

from dotenv import load_dotenv
import os
from traceback import format_exc
from typing import Final, Generator, Optional
from datetime import datetime
import time
from argparse import ArgumentParser, Namespace
import warnings

from psycopg import Connection, connect
from psycopg import errors as pg_errors
from psycopg.conninfo import make_conninfo
from psycopg.sql import SQL, Identifier

import httpx
from auxillary.utils import bcrypt_hash_password

from resource_server.config.app_config import AppConfig
from resource_server.dependencies import get_app_config

parser: ArgumentParser = ArgumentParser(
    description="CLI tool for populating the database"
)
parser.add_argument("-d", "--debug", default=False, action="store_true")
parser.add_argument("-env", "--env-path")
parser.add_argument("-ac", "--anime-count", default=100, type=int)
parser.add_argument("-mi", "--max-iterations", default=100, type=int)
parser.add_argument("-sp", "--superuser_password")
parser.add_argument("-eg", "--exclude-genres", nargs="*")
parser.add_argument("-ft", "--fetch-timeout", default=1.5, type=float)
parser.add_argument("-lf", "--logs-fpath")

SAVEPOINT_SQL: Final[SQL] = SQL("""SAVEPOINT {}""")
ROLLBACK_SQL: Final[SQL] = SQL("""ROLLBACK TO {}""")


def _log_error(fpath: str, message: str) -> None:
    with open(fpath, "a+") as file:
        file.write(message)


def main(
    anime_count: int,
    env_path: str,
    max_iterations: Optional[int] = None,
    debug: bool = False,
    logs_fpath: Optional[str] = None,
    exclude_genres: Optional[list[str]] = None,
    superuser_password: Optional[str] = None,
    fetch_timeout: float = 1.5,
) -> int:
    """Populate the database
    Args:
        debug (bool): Write errors and messages to a log file
        anime_count (int): Number of animes to insert into the database
        max_iterations (int): Maximum number of API calls to make before closing the script. Useful for setting an upper bound for running time in case of errors
        superuser_password (Optional[str]): Password for TENJIN superuser account
        exclude_genres (Optional[list[str]]): Genres to exclude when fetching animes
        fetch_timeout (float): Seconds to wait before moving onto another API call

    Raises:
        FileNotFoundError: If use_env is True and environment variables could not be loaded, or if config file could not be opened
        AssertionError: Schema mismatch between this function's REQUIRED_ENTITIES set and the entities in the config file

    Returns:
        Number of animes that were actually inserted in the database
    """
    if not load_dotenv(env_path):
        raise FileNotFoundError(env_path)

    URL: Final[str] = "https://api.jikan.moe/v4/anime/{id}/full"
    exclude_genres = exclude_genres or []
    # Update max_iterations if needed
    if not max_iterations:
        max_iterations = anime_count
    elif max_iterations and max_iterations < anime_count:
        warnings.warn(
            "max_iterations set below anime_count, incrementing...",
            category=UserWarning,
        )
        max_iterations = anime_count

    if not logs_fpath:
        logs_fpath = os.path.join(os.path.dirname(__file__), "genesis.logs")

    config: Final[AppConfig] = get_app_config()
    conninfo = make_conninfo(
        user=os.environ["SUPERUSER_POSTGRES_USERNAME"],
        password=os.environ["SUPERUSER_POSTGRES_PASSWORD"],
        host=str(config.DATABASE.POSTGRES_HOST),
        port=config.DATABASE.POSTGRES_PORT,
        dbname=config.DATABASE.POSTGRES_DATABASE,
    )
    connection: Final[Connection] = connect(conninfo)
    with connection.cursor() as cursor:
        latest_valid_checkpoint: int = 1
        forums_created: set[int] = set()

        # Create Tenjin superuser if not exists
        cursor.execute("SELECT * FROM users WHERE username = 'TENJIN';")
        op = cursor.fetchone()
        if not op:
            pw_hash: bytes = bcrypt_hash_password(
                superuser_password or os.environ["TENJIN_SUPERUSER_PW"]
            )

            cursor.execute(
                """INSERT INTO users (id_, username, email, pw_hash, time_joined)
                        VALUES (1, 'TENJIN', 'TENJIN@tenjin.org', %s, %s)""",
                (
                    pw_hash,
                    datetime.now(),
                ),
            )
            connection.commit()

        # Get base info
        cursor.execute("SELECT DISTINCT name_, id_ FROM genres;")
        existing_genres: dict[str, int] = {
            name: _id for name, _id in (cursor.fetchall() or ())
        }
        genres_to_add: set[int] = set()

        cursor.execute("BEGIN TRANSACTION;")
        cursor.execute(SAVEPOINT_SQL.format(Identifier(str(latest_valid_checkpoint))))

        # Begin fetching data
        animes_inserted: int = 0
        with httpx.Client() as http_client:
            for anime_id in range(1, max_iterations + 1):
                # Check if this anime exists already
                cursor.execute("SELECT id_ FROM animes WHERE id_ = %s", (anime_id,))
                if cursor.fetchone():
                    print(f"Anime with id {anime_id} already exists, skipping...")
                    continue
                print(f"Fetching data for anime with ID:", anime_id)

                response: httpx.Response = http_client.get(URL.format(id=anime_id))
                if response.is_error:
                    if debug:
                        jsonified_response: dict[str, str | int] = response.json()
                        _log_error(
                            logs_fpath,
                            f'Anime: {anime_id} - Code: {response.status_code}, Message: {jsonified_response.get("message", "N/A")}\n',
                        )
                    time.sleep(fetch_timeout)
                    continue

                DATA: dict[str, dict] = response.json()

                ANIME_INFO: dict[str, str | int | dict] = {
                    "title": DATA["data"]["titles"][0]["title"],
                    "members": 0,
                    "synopsis": DATA["data"]["synopsis"],
                    "stream_links": {
                        item["name"]: item["url"] for item in DATA["data"]["streaming"]
                    },
                }

                # Make new genre if not exists
                GENRES: list[str] = list(
                    map(lambda x: x["name"], DATA["data"]["genres"])
                )
                exclusion: bool = False
                for genre in GENRES:
                    if genre in exclude_genres:
                        exclusion = True
                        break
                    genre_id = existing_genres.get(genre)
                    if not genre_id:
                        cursor.execute(
                            "INSERT INTO genres (name_) VALUES (%s) RETURNING id_;",
                            (genre,),
                        )
                        genre_id = cursor.fetchone()[0]  # type: ignore
                        existing_genres[genre] = genre_id
                        connection.commit()
                    genres_to_add.add(genre_id)

                if exclusion:
                    time.sleep(fetch_timeout)
                    continue

                stream_links: Generator[tuple[int, str, str], None, None] = (
                    (anime_id, url, website)
                    for website, url in ANIME_INFO["stream_links"].items()  # type: ignore
                )

                try:
                    cursor.execute(
                        "INSERT INTO animes (id_, title, members, synopsis) VALUES (%s, %s, %s, %s);",
                        (
                            anime_id,
                            ANIME_INFO["title"],
                            ANIME_INFO["members"],
                            ANIME_INFO["synopsis"],
                        ),
                    )
                    # Bulk insert stream links
                    cursor.executemany(
                        "INSERT INTO stream_links VALUES (%s, %s, %s);", stream_links
                    )

                    # Bulk insert anime_genres
                    cursor.executemany(
                        "INSERT INTO anime_genres VALUES (%s, %s)",
                        ((anime_id, genre_id) for genre_id in genres_to_add),
                    )

                    # Make corresponding forum
                    cursor.execute(
                        f"""INSERT INTO forums (name_, parent_anime, description, created_at)
                                   VALUES (%s, %s, %s, %s) RETURNING id_;""",
                        (
                            ANIME_INFO["title"],
                            anime_id,
                            f"auto-generated forum by Tenjin for {ANIME_INFO['title']}".capitalize(),
                            datetime.now(),
                        ),
                    )

                    forum_id: int = cursor.fetchone()[0]  # type: ignore
                    forums_created.add(forum_id)

                    latest_valid_checkpoint += 1
                    cursor.execute(
                        SAVEPOINT_SQL.format(Identifier(str(latest_valid_checkpoint)))
                    )
                    genres_to_add.clear()

                    animes_inserted += 1
                    if animes_inserted == anime_count:
                        break

                except (
                    pg_errors.ConnectionException,
                    pg_errors.ConnectionFailure,
                ) as e:
                    print(f"Connection Failure, terminating script...")
                    with open("error_logs.txt", "a+") as logFile:
                        logFile.write(f"{datetime.now()}: {e.__class__.__name__}")
                        exit(200)

                except Exception as e:
                    cursor.execute(
                        ROLLBACK_SQL.format(Identifier(str(latest_valid_checkpoint)))
                    )
                    print(
                        f"Failed to insert anime {ANIME_INFO['title']} with ID {anime_id}, exception: {e.__class__.__name__}"
                    )
                    if debug:
                        print()
                        print(format_exc())
                        print()
                    continue

                time.sleep(fetch_timeout)

        connection.commit()

        # Add Tenjin superuser as admin in all forums
        cursor.executemany(
            "INSERT INTO forum_admins VALUES (%s, %s, %s);",
            ((forum_id, 1, "owner") for forum_id in forums_created),
        )
        connection.commit()
        cursor.execute("END TRANSACTION;")

        return animes_inserted


if __name__ == "__main__":
    args: Namespace = parser.parse_args()
    if args.superuser_password:
        warnings.warn(
            "TENNIN superuser password being passed as CLI argument may not be safe!",
            category=UserWarning,
        )

    main(**dict(args._get_kwargs()))
