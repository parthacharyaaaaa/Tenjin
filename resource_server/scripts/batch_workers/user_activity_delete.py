'''Mass soft delete a given user's contributions on account deletion
Resources soft deleted:
- users
- posts
- comments
'''

import os
from dotenv import load_dotenv
from redis import Redis
from redis import Redis, exceptions as redisExceptions
import psycopg2 as pg
from psycopg2.extras import execute_batch
from time import sleep
from traceback import format_exc
from datetime import datetime
import json

if __name__ == "__main__":
    loaded = load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"))
    if not loaded:
        raise FileNotFoundError()

    ID: int = os.getpid()

    interface: Redis = Redis(os.environ["REDIS_HOST"], int(os.environ["REDIS_PORT"]), decode_responses=True)
    if not interface.ping():
        raise ConnectionError()

    CONNECTION_KWARGS : dict[str, int | str] = {
        "user" : os.environ["POSTGRES_USERNAME"],
        "password" : os.environ["POSTGRES_PASSWORD"],
        "host" : os.environ["POSTGRES_HOST"],
        "port" : int(os.environ["POSTGRES_PORT"]),
        "database" : os.environ["POSTGRES_DATABASE"]
    }

    try:
        CONNECTION: pg.extensions.connection = pg.connect(**CONNECTION_KWARGS)
    except Exception as e:
        print(f"{ID}: Failed to connect to Postgres instance.\n\tError: {e.__class__.__name__}\n\tError Logs: ", format_exc())
        exit(500)

    SOFT_DELETION_SQL: str =  '''UPDATE {tablename} SET deleted_at = %s, deleted = true WHERE id = %s'''
    rtfb_data: list[tuple[int, datetime]] = []
    nrtfb_data: list[tuple[int, datetime]] = []

    # Initally load stream name
    with open(os.path.join(os.path.dirname(__file__), "worker_config.json"), 'rb') as configFile:
        configData: dict = json.loads(configFile.read())
        wait: float = configData.get("wait", 1)
        backoffSequence: list[float] = configData.get("backoff_seq", [0.1, 0.5, 1, 2, 3])
        backoffIndex, maxBackoffIndex = 0, len(backoffSequence) - 1
        streamName: str = configData["user_activity_delete_stream"]
        batchSize: int = configData["user_activity_delete_batch_size"]
    with CONNECTION:
        with CONNECTION.cursor() as CURSOR:
            while(True):
                try:
                    _streamd_queries: list[tuple[str, dict[str, str]]] = interface.xrange(streamName, count=batchSize)
                except (redisExceptions.ConnectionError):
                    if backoffIndex >= maxBackoffIndex:
                        print(f"[{ID}]: Connection to Redis instance compromised, exiting...")
                        exit(100)
                    sleep(wait)
                    backoffIndex+=1
                    continue

                backoffIndex = 0
                if not _streamd_queries:
                    sleep(wait)
                    continue

                trimUBs: str = _streamd_queries[-1][0].split("-")
                trimUB: str = '-'.join((trimUBs[0], str(int(trimUBs[1]) + 1)))

                for querydata in _streamd_queries:
                    # time of stream entry will be taken as time of soft deletion
                    deleted_at: datetime = datetime.fromtimestamp(float(querydata[0].split("-")[0]))    # mfw I'm so bad at programming that even Python becomes unreadable
                    rtfb: int = int(querydata[1].pop('rtfb', 0))
                    userID: int = int(querydata[1].pop('id'))

                    if rtfb:
                        rtfb_data.append((deleted_at, userID))
                    else:
                        nrtfb_data.append((deleted_at, userID))

                # For both batches of users, update users table
                try:
                    execute_batch(cur=CURSOR,
                                  sql=SOFT_DELETION_SQL.format(tablename='users'),
                                  argslist=rtfb_data+nrtfb_data)
                    nrtfb_data.clear()
                    
                    # For RTFB users, update users table and posts+comments tables
                    execute_batch(cur=CURSOR,
                                  sql=SOFT_DELETION_SQL.format(tablename='posts'),
                                  argslist=rtfb_data)
                    execute_batch(cur=CURSOR,
                                  sql=SOFT_DELETION_SQL.format(tablename='comments'),
                                  argslist=rtfb_data)
                    rtfb_data.clear()

                    CONNECTION.commit()
                except (pg.errors.ModifyingSqlDataNotPermitted):
                    print(f"[{ID}]: Permission error, aborting script...")
                    exit(500)
                except (pg.errors.SyntaxError, pg.errors.AmbiguousColumn, pg.errors.AmbiguousParameter) as e:
                    print(f"[{ID}]: SQL invalid, aborting script, please manually resolve insertion logic...")
                    print(f"[{ID}]: Traceback: {e.__class__.__name__}\n{format_exc()}")
                    exit(500)
                except (pg.errors.FdwTableNotFound, pg.errors.UndefinedTable):
                    print(f"[{ID}]: Table not found")
                except pg.errors.Error as pg_error:
                    print(f"[{ID}]: Error in executing batch update, exception: {pg_error.__class__.__name__}")
                    print(f"[{ID}]: Error details: {format_exc()}")

                interface.xtrim(streamName, minid=trimUB)