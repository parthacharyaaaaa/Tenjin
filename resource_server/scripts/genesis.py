'''### Populates the database

#### Tables populated:

- Animes
- Forums (1 per anime)
- Genres
- Anime genres
- stream_links
- forum_admins (`owner` as **Tenjin superuser**)
'''
import psycopg2 as pg
from dotenv import load_dotenv
import requests
import json
import os
from hashlib import pbkdf2_hmac
from traceback import format_exc
from typing import Generator, Optional
from datetime import datetime
import time
from argparse import ArgumentParser, Namespace
import warnings

parser: ArgumentParser = ArgumentParser(description='CLI tool for populating the database')
parser.add_argument('-d', '--debug', default=False, action='store_true')
parser.add_argument('-env', '--use-env', default=True, action='store_true')
parser.add_argument('-ac', '--anime-count', default=100, type=int)
parser.add_argument('-mi', '--max-iterations', default=100, type=int)
parser.add_argument('-sp', '--superuser_password')
parser.add_argument('-eg', '--exclude-genres', nargs="*")
parser.add_argument('-ft', '--fetch-timeout', default=1.5, type=float)
parser.add_argument('-ov', '--override_env', action='store_true', default=True)
parser.add_argument('-lf', '--logs-fpath')
parser.add_argument('-cf', '--config-fpath')

def _log_error(fpath: str, message: str) -> None:
    with open(fpath, 'a+') as file:
        file.write(message)
    
def main(anime_count: int, max_iterations: Optional[int] = None, use_env: bool = True, override_env: bool = True, debug: bool = False, logs_fpath: Optional[str] = None, connection_kwargs: Optional[dict[str, str|int]] = None, config_fpath: Optional[str] = None, exclude_genres: Optional[list[str]] = None, superuser_password: Optional[str] = None, fetch_timeout: float = 1.5) -> int:
    '''Populate the database
    Args:
        use_env (bool): Whether to load configuration from the environment, or rely on connection_kwargs and config_fpath arguments
        debug (bool): Write errors and messages to a log file
        anime_count (int): Number of animes to insert into the database
        max_iterations (int): Maximum number of API calls to make before closing the script. Useful for setting an upper bound for running time in case of errors
        connection_kwargs (Optional[dict[str, str|int]): Dictionary containing connection items for Postgres
        config_fpath (Optional[str]): Absolute filepath for config file
        superuser_password (Optional[str]): Password for TENJIN superuser account
        exclude_genres (Optional[list[str]]): Genres to exclude when fetching animes
        fetch_timeout (float): Seconds to wait before moving onto another API call

    Raises:
        FileNotFoundError: If use_env is True and environment variables could not be loaded, or if config file could not be opened
        AssertionError: Schema mismatch between this function's REQUIRED_ENTITIES set and the entities in the config file
    
    Returns:
        Number of animes that were actually inserted in the database
    '''
    
    REQUIRED_ENTITIES: frozenset[str] = frozenset({"animes", "forums", "genres", "anime_genres", "stream_links", "forum_admins"})

    # Update max_iterations if needed
    if max_iterations and max_iterations < anime_count:
        warnings.warn('max_iterations set below anime_count, incrementing...', category=UserWarning)
        max_iterations = anime_count
    if not max_iterations:
        max_iterations = anime_count
    
    if not logs_fpath:
        logs_fpath: str = os.path.join(os.path.dirname(__file__), 'genesis_logs.txt')

    if use_env:
        loaded: bool = load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"), override=override_env)
        if not loaded:
            raise FileNotFoundError()
        
        if not config_fpath:
            config_fpath = os.path.join(os.path.dirname(os.path.dirname(__file__)), "instance", os.environ['CONFIG_FILE'])
        if not connection_kwargs:
            connection_kwargs = {
                "user" : os.environ["SUPERUSER_POSTGRES_USERNAME"],
                "password" : os.environ["SUPERUSER_POSTGRES_PASSWORD"],
                "host" : os.environ["RESOURCE_SERVER_POSTGRES_HOST"],
                "port" : int(os.environ["RESOURCE_SERVER_POSTGRES_PORT"]),
                "database" : os.environ["RESOURCE_SERVER_POSTGRES_DATABASE"]
                }

    with open(config_fpath, "rb") as configFile:
        DB_DATA: dict[str, dict] = json.loads(configFile.read())['database']
        if not exclude_genres:
            exclude_genres: list[str] = DB_DATA['business']['excluded_genres']
        else:
            exclude_genres: list[None] = []

    ENTITIES: frozenset[str] = frozenset(entity for entities in DB_DATA['entities'].values() for entity in entities)
    assert not REQUIRED_ENTITIES - ENTITIES, "Invalid database/configuration schema for this script. Either update this script or alter database schema or configuration file accordingly"

    CONNECTION: pg.extensions.connection = pg.connect(**connection_kwargs)
    URL: str = "https://api.jikan.moe/v4/anime/{id}/full"
    animes_inserted: int = 0

    with CONNECTION.cursor() as cursor:
        latest_valid_checkpoint: int = 1
        forums_created: set[int] = set()

        # Create Tenjin superuser if not exists
        cursor.execute("SELECT * FROM users WHERE username = 'TENJIN';")
        op = cursor.fetchone()
        if not op:
            salt: bytes = os.urandom(16)
            pw_hash: bytes = pbkdf2_hmac(hash_name='sha256', salt=salt, iterations=100000,
                                         password=superuser_password.encode() if superuser_password else os.environ['TENJIN_SUPERUSER_PW'].encode())

            cursor.execute('''INSERT INTO users (id, username, email, pw_hash, pw_salt, time_joined)
                        VALUES (1, 'TENJIN', 'TENJIN@tenjin.org', %s, %s, %s)''',
                        (pw_hash, salt, datetime.now(),))
            CONNECTION.commit()

        # Get base info
        cursor.execute("SELECT DISTINCT _name, id FROM genres;") 
        existing_genres: dict[int, str] = {name: _id for name, _id in (cursor.fetchall() or ())}
        genres_to_add: set[int] = set()

        cursor.execute("BEGIN TRANSACTION;")
        cursor.execute(f"SAVEPOINT s_{latest_valid_checkpoint};")

        # Begin fetching data
        with requests.sessions.Session() as fetchSession:
            for anime_id in range(1, max_iterations+1):
                # Check if this anime exists already
                cursor.execute("SELECT id FROM animes WHERE id = %s", (anime_id,))
                if cursor.fetchone():
                    print(f"Anime with id {anime_id} already exists, skipping...")
                    continue
                print(f"Fetching data for anime with ID:", anime_id)

                response: requests.Response = fetchSession.get(URL.format(id=anime_id))
                if not response.ok:
                    if debug:
                        jsonified_response: dict[str, str|int] = response.json()
                        _log_error(logs_fpath, f'Anime: {anime_id} - Code: {response.status_code}, Message: {jsonified_response.get("message", "N/A")}\n')
                    time.sleep(fetch_timeout)
                    continue

                DATA: dict[str, dict] = response.json()
                if DATA.get('status', 200) != 200:
                    print(DATA)
                    if debug:
                        _log_error(logs_fpath, f'Anime: {anime_id} - Code: {response.status_code}, Message: {DATA.get("message", "N/A")}\n')
                    time.sleep(fetch_timeout)
                    continue

                ANIME_INFO: dict[str, str | int | dict] = {"title": DATA['data']['titles'][0]['title'],
                                                            "rating": DATA['data']['score'],
                                                            "mal_ranking": DATA['data']['rank'],
                                                            "members": 0,
                                                            "synopsis": DATA['data']['synopsis'],
                                                            "stream_links": {item['name']: item['url'] for item in DATA['data']['streaming']}}
                
                # Make new genre if not exists
                GENRES: list[str] = list(map(lambda x: x['name'], DATA["data"]['genres']))
                exclusion: bool = False
                for genre in GENRES:
                    if genre in exclude_genres:
                        exclusion = True
                        break
                    genre_id = existing_genres.get(genre)
                    if not genre_id:
                        cursor.execute("INSERT INTO genres (_name) VALUES (%s) RETURNING id;", (genre,))
                        genre_id = cursor.fetchone()[0]
                        existing_genres[genre] = genre_id
                        CONNECTION.commit()
                    genres_to_add.add(genre_id)

                if exclusion:
                    time.sleep(fetch_timeout)
                    continue

                stream_links: Generator[tuple[int, str, str], None, None] = ((anime_id, url, website) for website, url in ANIME_INFO['stream_links'].items())
        
                try:
                    cursor.execute("INSERT INTO animes (id, title, rating, mal_ranking, members, synopsis) VALUES (%s, %s, %s, %s, %s, %s);", (anime_id,
                                                                                    ANIME_INFO["title"],
                                                                                    ANIME_INFO['rating'],
                                                                                    ANIME_INFO['mal_ranking'],
                                                                                    ANIME_INFO['members'],
                                                                                    ANIME_INFO['synopsis'],))
                    # Bulk insert stream links
                    cursor.executemany("INSERT INTO stream_links VALUES (%s, %s, %s);", stream_links)

                    # Bulk insert anime_genres
                    cursor.executemany("INSERT INTO anime_genres VALUES (%s, %s)", ((anime_id, genre_id) for genre_id in genres_to_add))
                    
                    # Make corresponding forum
                    # Even the Python gods will curse me when they see this, I'll repent for this by starting with C++ in a few months
                    cursor.execute(f'''INSERT INTO forums (_name, anime, description, created_at)
                                   VALUES (%s, %s, %s, %s) RETURNING id;''', 
                                   (ANIME_INFO['title'], anime_id, f"auto-generated forum by Tenjin for {ANIME_INFO['title']}".capitalize(), 
                                    datetime.now()))

                    forum_id: int = cursor.fetchone()[0]
                    forums_created.add(forum_id)

                    latest_valid_checkpoint+=1
                    cursor.execute(f'SAVEPOINT s_{latest_valid_checkpoint};')
                    genres_to_add.clear()

                    animes_inserted+=1
                    if animes_inserted == anime_count:
                        break

                except (pg.errors.ConnectionException, pg.errors.ConnectionFailure) as e:
                    print(f"Connection Failure, terminating script...")
                    with open("error_logs.txt", "a+") as logFile:
                        logFile.write(f"{datetime.now()}: {e.__class__.__name__}")
                        exit(200)

                except Exception as e:
                    cursor.execute(f"ROLLBACK TO s_{latest_valid_checkpoint}")
                    print(f"Failed to insert anime {ANIME_INFO['title']} with ID {anime_id}, exception: {e.__class__.__name__}")
                    if debug:
                        print()
                        print(format_exc())
                        print()
                    continue

                time.sleep(fetch_timeout)

        CONNECTION.commit()

        # Add Tenjin superuser as admin in all forums
        cursor.executemany("INSERT INTO forum_admins VALUES (%s, %s, %s);", ((forum_id, 1, 'owner') for forum_id in forums_created))
        CONNECTION.commit()
        cursor.execute("END TRANSACTION;")
        
        return animes_inserted

if __name__ == '__main__':
    args: Namespace = parser.parse_args()
    if args.superuser_password:
        warnings.warn("TENNIN superuser password being passed as CLI argument may not be safe!", category=UserWarning)
    
    main(**dict(args._get_kwargs()))