import logging
import os
import time
import warnings
from argparse import ArgumentParser, Namespace
from traceback import format_exc
from typing import Final, Optional

import httpx
from auxillary.security.hashing import bcrypt_hash_password
from dotenv import load_dotenv
from resource_server.dependencies import get_sync_database_session_maker
from resource_server.models.database import (
    Anime,
    AnimeGenre,
    Forum,
    ForumAdmin,
    Genre,
    StreamLink,
    User,
)
from resource_server.models.database_enums import AdminRoles
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.expression import insert, select

parser: ArgumentParser = ArgumentParser(
    description="CLI tool for populating the database"
)
parser.add_argument("-d", "--debug", default=False, action="store_true")
parser.add_argument("-env", "--env-path")
parser.add_argument("-ac", "--anime-count", default=100, type=int)
parser.add_argument("-mi", "--max-iterations", default=100, type=int)
parser.add_argument("-sp", "--superuser-password")
parser.add_argument("-eg", "--exclude-genres", nargs="*")
parser.add_argument("-ft", "--fetch-timeout", default=1.5, type=float)


def main(
    anime_count: int,
    env_path: str = ".env",
    debug: bool = False,
    exclude_genres: Optional[list[str]] = None,
    superuser_password: Optional[str] = None,
    fetch_timeout: float = 1.5,
    logger: logging.Logger | None = None,
    max_iterations: int | None = None,
) -> int:
    if not load_dotenv(env_path):
        raise FileNotFoundError(env_path)

    uri_template: Final[str] = "https://api.jikan.moe/v4/anime/{id}/full"
    exclude_genres = exclude_genres or []
    logger = logger or logging.getLogger("genesis_dml_script")
    session_maker: Final[sessionmaker[Session]] = get_sync_database_session_maker()
    max_iterations = max_iterations or anime_count

    # Create Tenjin superuser if not exists
    with session_maker() as session:
        superuser: User | None = session.execute(
            select(User).where(User.username == "TENJIN")
        ).scalar_one_or_none()
        if not superuser:
            pw_hash: bytes = bcrypt_hash_password(
                superuser_password or os.environ["TENJIN_SUPERUSER_PW"]
            )
            session.execute(
                insert(User).values(
                    pw_hash=pw_hash, username="TENJIN", email="noreply@tenjin.org"
                )
            )
            session.commit()
            logger.info("Created missing TENJIN superuser")

    forums_created: list[int] = []
    animes_inserted: int = 0
    anime_id: int = 0
    with session_maker() as session:
        session.expire_on_commit = False
        existing_genres: dict[str, int] = {
            g.name_: g.id_
            for g in session.execute(select(Genre).distinct()).scalars().all()
        }

        with httpx.Client(timeout=fetch_timeout) as http_client:
            while animes_inserted < anime_count and anime_id < max_iterations:
                anime_id += 1
                # Check if this anime exists already
                existing_anime_id: int | None = session.execute(
                    select(Anime.id_).where(Anime.id_ == anime_id)
                ).scalar_one_or_none()
                if existing_anime_id is not None:
                    logger.info(f"Anime with id {anime_id} already exists, skipping...")
                    continue
                logger.info("Fetching data for anime with ID: %s", anime_id)

                response: httpx.Response = http_client.get(
                    uri_template.format(id=anime_id)
                )
                if response.is_error:
                    if debug:
                        jsonified_response: dict[str, str | int] = response.json()
                        logger.error(
                            f"Anime {anime_id} ({response.status_code}): {jsonified_response.get('message', 'N\\A')}"
                        )
                    time.sleep(fetch_timeout)
                    continue

                data: dict[str, dict] = response.json()
                anime_info: dict[str, str | int | dict] = {
                    "title": data["data"]["titles"][0]["title"],
                    "members": 0,
                    "synopsis": data["data"]["synopsis"],
                    "stream_links": {
                        item["name"]: item["url"] for item in data["data"]["streaming"]
                    },
                }

                genres: list[str] = list(
                    map(lambda x: x["name"], data["data"]["genres"])
                )
                genres_to_add: list[str] = []
                for i, genre in enumerate(genres.copy()):
                    if genre in exclude_genres:
                        genres.pop(i)
                    elif genre not in existing_genres:
                        genres_to_add.append(genre)

                if genres_to_add:
                    inserted_genre_ids = list(
                        session.execute(
                            insert(Genre).returning(Genre.id_),
                            tuple({"name_": genre} for genre in genres_to_add),
                        )
                        .scalars()
                        .all()
                    )
                    existing_genres.update(
                        {
                            genre: inserted_genre_ids[i]
                            for i, genre in enumerate(genres_to_add)
                        }
                    )
                    logger.info(f"Adds new genres: {', '.join(genres_to_add)}")
                    genres_to_add.clear()

                try:
                    session.execute(
                        insert(Anime).values(
                            id_=anime_id,
                            title=anime_info["title"],
                            synopsis=anime_info["synopsis"],
                        )
                    )
                    if anime_info["stream_links"]:
                        session.execute(
                            insert(StreamLink),
                            tuple(
                                {"anime_id": anime_id, "url": url, "website": website}
                                for website, url in anime_info["stream_links"].items()  # type: ignore
                            ),
                        )
                    session.execute(
                        insert(AnimeGenre),
                        tuple(
                            {"anime_id": anime_id, "genre_id": existing_genres[genre]}
                            for genre in genres
                        ),
                    )
                    forum_id: int = session.execute(
                        insert(Forum)
                        .values(
                            name_=anime_info["title"],
                            parent_anime=anime_id,
                            description=f"auto-generated forum by Tenjin for {anime_info['title']}".capitalize(),
                        )
                        .returning(Forum.id_)
                    ).scalar_one()

                    animes_inserted += 1
                    logger.info(
                        f"Added all details for anime {anime_info['title']}, pending commit"
                    )
                    forums_created.append(forum_id)
                    logger.info(
                        f"Anime {anime_info['title']}, and forum durably inserted (Anime ID: {anime_id})"
                    )
                except (DataError, IntegrityError) as e:
                    session.rollback()
                    logger.warning(
                        f"Failed to insert anime {anime_info['title']} with ID {anime_id}, exception: {e.__class__.__name__}"
                    )
                    if debug:
                        logger.debug(format_exc())
                except SQLAlchemyError:
                    session.rollback()  # Unnecessary
                    logger.exception(
                        "Unrecoverable database error while inserting anime %s "
                        "(ID: %s); terminating script",
                        anime_info["title"],
                        anime_id,
                    )
                    raise
                time.sleep(fetch_timeout)

        # Add Tenjin superuser as admin in all forums
        session.execute(
            insert(ForumAdmin),
            tuple(
                {"forum_id": forum_id, "user_id": 1, "role": AdminRoles.OWNER}
                for forum_id in forums_created
            ),
        )
        session.commit()

        return animes_inserted


if __name__ == "__main__":
    args: Namespace = parser.parse_args()

    logger = logging.getLogger("genesis_dml_script")
    logger.setLevel(logging.DEBUG if args.debug else logging.INFO)
    logger.addHandler(logging.StreamHandler())

    if args.superuser_password:
        warnings.warn(
            "TENNIN superuser password being passed as CLI argument may not be safe!",
            category=UserWarning,
            stacklevel=2,
        )

    main(**dict(args._get_kwargs()))
