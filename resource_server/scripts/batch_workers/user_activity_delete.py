'''Mass soft delete a given user's contributions on account deletion
Resources soft deleted:
- users
- posts
- comments
'''
import psycopg2 as pg
from psycopg2.extras import execute_batch
from redis import Redis
from redis import Redis, exceptions as redisExceptions
import os
from dotenv import load_dotenv
from time import sleep
from traceback import format_exc
from datetime import datetime
from typing import Any
import json
import toml

if __name__ == "__main__":
    loaded = load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"))
    if not loaded:
        raise FileNotFoundError()

    ID: int = os.getpid()

    redis_config_fpath: os.PathLike = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'config', os.environ['redis_config_filename'])
    if not os.path.isfile(redis_config_fpath):
        raise FileNotFoundError("Redis config toml file not found")
    
    redis_config_kwargs: dict[str, Any] = toml.load(f=redis_config_fpath)
    redis_config_kwargs.update({'username' : os.environ['BATCH_SERVER_REDIS_USERNAME'], 'password' : os.environ['BATCH_SERVER_REDIS_PASSWORD']})   # Inject login credentials through env
    interface: Redis = Redis(**redis_config_kwargs)

    CONNECTION_KWARGS : dict[str, int | str] = {
        "user" : os.environ["WORKER_POSTGRES_USERNAME"],
        "password" : os.environ["WORKER_POSTGRES_PASSWORD"],
        "host" : os.environ["RESOURCE_SERVER_POSTGRES_HOST"],
        "port" : int(os.environ["RESOURCE_SERVER_POSTGRES_PORT"]),
        "database" : os.environ["RESOURCE_SERVER_POSTGRES_DATABASE"]
    }

    try:
        CONNECTION: pg.extensions.connection = pg.connect(**CONNECTION_KWARGS)
    except Exception as e:
        print(f"{ID}: Failed to connect to Postgres instance.\n\tError: {e.__class__.__name__}\n\tError Logs: ", format_exc())
        exit(500)

    # Initialize SQL templates
    SOFT_DELETION_SQL: str =  '''UPDATE {tablename} SET deleted_at = %s, deleted = true WHERE id = %s'''
    RTBF_UPDATION_SQL: str = '''UPDATE {tablename} SET rtbf_hidden = true WHERE id IN ({ids_to_hide})'''

    # Initalize separate lists of ids, deleted_at pairs for RTBF and non-RTBF users
    rtbf_data: list[tuple[int, datetime]] = []
    non_rtbf_data: list[tuple[int, datetime]] = []

    # Initialize configurations for this worker
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
                        # For connection errors, try retrying at increasing backoff periods
                        print(f"[{ID}]: Connection to Redis instance compromised, exiting...")
                        exit(100)
                    sleep(backoffSequence[backoffIndex])
                    backoffIndex+=1
                    continue

                backoffIndex = 0    # Reset backoff index on succesfull network calls
                if not _streamd_queries:
                    sleep(wait)
                    continue

                # Remember upper bound of fetched substream for trimming later
                trimUBs: str = _streamd_queries[-1][0].split("-")
                trimUB: str = '-'.join((trimUBs[0], str(int(trimUBs[1]) + 1)))

                for querydata in _streamd_queries:
                    # time of stream entry will be taken as time of soft deletion
                    deleted_at: datetime = datetime.fromtimestamp(float(querydata[0].split("-")[0]))    # mfw I'm so bad at programming that even Python becomes unreadable
                    rtbf: int = int(querydata[1].pop('rtbf', 0))
                    userID: int = int(querydata[1].pop('id'))

                    if rtbf:
                        rtbf_data.append((deleted_at, userID))
                    else:
                        non_rtbf_data.append((deleted_at, userID))

                # For both batches of users, update users table
                try:
                    execute_batch(cur=CURSOR,
                                  sql=SOFT_DELETION_SQL.format(tablename='users'),
                                  argslist=rtbf_data+non_rtbf_data)
                    non_rtbf_data.clear()
                    
                    # For RTFB users exclusively, update posts+comments tables
                    rtbf_ids_str: str = ','.join(str(entry[0]) for entry in rtbf_data)   # Fetch and format only user IDs for RTBF users
                    execute_batch(cur=CURSOR,
                                  sql=RTBF_UPDATION_SQL.format(tablename='posts', ids_to_hide=rtbf_ids_str))
                    execute_batch(cur=CURSOR,
                                  sql=RTBF_UPDATION_SQL.format(tablename='comments', ids_to_hide=rtbf_ids_str))
                    rtbf_data.clear()

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

                interface.xtrim(streamName, minid=trimUB)   # Trim consumed substream
                sleep(wait)