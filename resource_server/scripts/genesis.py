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
import os
import json
import sys
from traceback import format_exc
from typing import Generator
from enum import Enum
from hashlib import pbkdf2_hmac
from datetime import datetime

class AdminRoles(Enum):
    admin = "admin"
    superuser = "super"
    owner = "owner"
    
DEBUG : bool = "--debug" in sys.argv
REQUIRED_ENTITIES : frozenset[str] = frozenset({"animes", "forums", "genres", "anime_genres", "stream_links", "forum_admins"})

loaded = load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
if not loaded:
    raise FileNotFoundError()

with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "instance", os.environ['CONFIG_FILE']), "rb") as configFile:
    DB_DATA : dict[str, dict] = json.loads(configFile.read())['database']

ENTITIES : frozenset[str] = frozenset(entity for entities in DB_DATA['entities'].values() for entity in entities)

assert not REQUIRED_ENTITIES - ENTITIES, "Invalid database/configuration schema for this script. Either update this script or alter database schema or configuration file accordingly"


CONNECTION_KWARGS : dict[str, int | str] = {
    "user" : os.environ["POSTGRES_USERNAME"],
    "password" : os.environ["POSTGRES_PASSWORD"],
    "host" : os.environ["POSTGRES_HOST"],
    "port" : int(os.environ["POSTGRES_PORT"]),
    "database" : os.environ["POSTGRES_DATABASE"]
}

CONNECTION : pg.extensions.connection = pg.connect(**CONNECTION_KWARGS)

ANIMES : int = int(DB_DATA['business']['anime_count'])
URL : str = "https://api.jikan.moe/v4/anime/{}/full"

def addGenre(cursor : pg.extensions.cursor, genre) -> int:
    cursor.execute("INSERT INTO genres (_name) VALUES (%s) RETURNING id;", (genre,))
    return cursor.fetchone()[0]

# Alright, here's how this will go
with CONNECTION.cursor() as cursor:
    latestValidCheckpoint : int = 0
    forumsCreated : list[int] = []

    # Create Tenjin superuser if not exists
    cursor.execute("SELECT * FROM users WHERE username = TENJIN;")
    op = cursor.fetchone()
    if not op:
        salt : bytes = os.urandom(16)
        passwordHash : bytes = pbkdf2_hmac('sha256', os.environ['TENJIN_SUPERUSER_PW'].encode(), salt, 100000)

        cursor.execute("INSERT INTO users VALUES (1, 'TENJIN', 'TENJIN', 'TENJIN@tenjin.org', null, %s, %s, 0, 0, 0, %s, null, false, null)", (passwordHash, salt, datetime.now(),))
        CONNECTION.commit()

    # Get base info
    cursor.execute("SELECT DISTINCT(_name, id) FROM genres;") 
    _genres : tuple[tuple[int, str]] = cursor.fetchall() or ()
    existingGenres : dict[int, str] = {name : _id for name, _id in _genres}
    del _genres
    print(f"{existingGenres=}")
    genresToAdd : list[int] = []

    cursor.execute("BEGIN TRANSACTION;")
    cursor.execute(f"SAVEPOINT s_{latestValidCheckpoint};")

    with requests.session() as fetchSession:
        for animeID in range(1,1070):
            fetchResponse : requests.Response = fetchSession.get(URL.format(animeID))
            if not fetchResponse.ok:
                print("Failed to fetch data for anime ID:", animeID)
                continue
            DATA : dict[str, dict] = fetchResponse.json()
            ANIME_INFO : dict[str, str | int | dict] = {"title" : DATA['data']['titles'][0]['title'],
                                                        "rating" : DATA['data']['score'],
                                                        "mal_ranking" : DATA['data']['rank'],
                                                        "members" : 0,
                                                        "synopsis" : DATA['data']['synopsis'],
                                                        "stream_links" : {item['name'] : item['url'] for item in DATA['data']['streaming']}}
            
            # Make new genre if not exists
            GENRES : list[str] = list(map(lambda x : x['name'], DATA["data"]['genres']))
            for genre in GENRES:
                genreID = existingGenres.get(genre)
                if not genreID:
                    genreID = addGenre(cursor, genre)
                    CONNECTION.commit()
                genresToAdd.append(genreID)

            # Prep stream links in a single generator
            streamLinks : Generator[tuple[int, str, str]] = ((animeID, url, website) for website, url in ANIME_INFO['stream_links'].items())
    
            try:
                cursor.execute("INSERT INTO animes (id, title, rating, mal_ranking, members, synopsis) VALUES (%s, %s, %s, %s, %s);", (animeID,
                                                                                ANIME_INFO["title"],
                                                                                ANIME_INFO['rating'],
                                                                                ANIME_INFO['mal_ranking'],
                                                                                ANIME_INFO['members'],
                                                                                ANIME_INFO['synopsis'],))
                # Bulk insert stream links
                cursor.executemany("INSERT INTO stream_links VALUES (%s, %s, %s);", streamLinks)

                # Bulk insert anime_genres
                cursor.executemany("INSERT INTO anime_genres VALUES (%s, %s)", ((animeID, genreID) for genreID in genresToAdd))
                
                # Make corresponding forum
                cursor.execute(f"INSERT INTO forums (_name, anime, description, subscribers, posts, created_at, admin_count) VALUES ({','.join(['%s']*7)}) RETURNING id;", (ANIME_INFO['title'], ANIME_INFO['title'], f"auto-generated forum by Tenjin for {ANIME_INFO['title']}".capitalize(), 0, 0, datetime.now(), 1))        # Even the Python gods will curse me when they see this, I'll repent for this by starting with C in a few months

                forumID : int = cursor.fetchone()[0]

                forumsCreated.append(forumID)
                latestValidCheckpoint+=1
                cursor.execute(f'SAVEPOINT s_{latestValidCheckpoint};')
                genresToAdd = []

            except Exception as e:
                cursor.execute(f"ROLLBACK TO s_{latestValidCheckpoint-1}")
                print(f"Failed to insert anime {ANIME_INFO['title']} with ID {animeID}, exception: {e.__class__.__name__}")
                if DEBUG:
                    print()
                    print(format_exc())
                    print()
                continue

    
    cursor.executemany("INSERT INTO forum_admins VALUES (%s, %s, %s);", ((forumID, 1, AdminRoles.owner) for forumID in forumsCreated))
    CONNECTION.commit()
    cursor.execute("END TRANSACTION;")
    