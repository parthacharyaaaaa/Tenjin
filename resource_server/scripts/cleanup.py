'''Hard deletes expired users from the database, considers RTBF too

Note: This can be quite a heavy action depending on how many cascades it ends up in, based on the RTBF flag and the amount of posts+comments per deleted user, as well as the amount of comments for them too
'''
import os
from dotenv import load_dotenv
import psycopg2 as pg
from datetime import datetime, timedelta
from typing import Generator

loaded = load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
if not loaded:
    raise FileNotFoundError()

def generateUsersHitlist(fetchedResult : list[tuple[int]]):
    for _res in fetchedResult:
        yield(_res)

KILL_WINDOW_UB : datetime = (datetime.now() - timedelta(days=int(os.environ['ACCOUNT_RECOVERY_PERIOD']))).replace(hour=0, minute=0, second=0, microsecond=0)         # Maybe shift to usign config.json?

CONNECTION_KWARGS : dict[str, int | str] = {
    "user" : os.environ["POSTGRES_USERNAME"],
    "password" : os.environ["POSTGRES_PASSWORD"],
    "host" : os.environ["POSTGRES_HOST"],
    "port" : int(os.environ["POSTGRES_PORT"]),
    "database" : os.environ["POSTGRES_DATABASE"]
}


CONNECTION : pg.extensions.connection = pg.connect(**CONNECTION_KWARGS)
CONNECTION_KWARGS = None

with CONNECTION.cursor() as dbCursor:
    dbCursor.execute("SELECT id, rtfb FROM users WHERE deleted = true AND time_deleted < %s", (KILL_WINDOW_UB,))
    hitlistData : Generator[tuple[int, bool]]= generateUsersHitlist(dbCursor.fetchall())

    for user in hitlistData:
        if user[1]:
            # RTFB true, delete all posts and comments
            dbCursor.executemany("DELETE from posts WHERE author_id = %s;", (user[0],))     # DDL would cascade deletion of all child comments and entries in post_saves, post_reports, post_votes for this post we good

