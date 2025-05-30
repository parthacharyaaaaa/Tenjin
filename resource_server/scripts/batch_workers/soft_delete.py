'''Perform soft deletion on records.
Tables affected:
- posts
- comments
- forums
- users

Works on the assumption that a soft deletion is represented as a true value in the `deleted` column, accompanied with a TIMESTAMP value in `deleted_ar`'''

import os
import json
from dotenv import load_dotenv
from time import sleep
from datetime import datetime
import time
import psycopg2 as pg
from psycopg2.extras import execute_batch
from traceback import format_exc

from redis import Redis
from redis import exceptions as redisExceptions

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

CONNECTION: pg.extensions.connection = pg.connect(**CONNECTION_KWARGS)
CURSOR: pg.extensions.cursor = CONNECTION.cursor()
UPDATION_SQL: str = "UPDATE {tablename} SET deleted_at = %s, deleted = true WHERE id IN ({ids_to_delete})"
if __name__ == '__main__':
    # Initally load stream name
    with open(os.path.join(os.path.dirname(__file__), "worker_config.json"), 'rb') as configFile:
        configData: dict = json.loads(configFile.read())
        wait: float = configData.get("wait", 1)
        backoffSequence: list[float] = configData.get("backoff_seq", [0.1, 0.5, 1, 2, 3])
        backoffIndex, maxBackoffIndex = 0, len(backoffSequence) - 1
        streamName: str = configData["soft_delete_stream"]
        batchSize: int = configData["soft_delete_batch_size"]
    
    with CONNECTION:
        with CURSOR:
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

                # Stream entries will contain unique ID, table name, and primary key of target record
                table_groups: dict[str, list[list[int], list[datetime]]] = {}
                for queryData in _streamd_queries:
                    # Prepare table groups with mappings containing table names as keys and list of 2 lists, with IDs to delete and their deleted timestamps as pairs at equal indices
                    try:
                        table: str = queryData[1].pop('table')
                        timestamp: float = float(queryData[0].split("-")[0])
                        deleted_at: datetime = datetime.fromtimestamp(timestamp)
                        if table in table_groups:
                            table_groups[table][0].append(queryData[1].pop('id'))
                            table_groups[table][1].append(deleted_at)
                        else:
                            table_groups[table][0] = queryData[1].pop('id')
                            table_groups[table][1] = [deleted_at]
                        
                    except KeyError:
                        print(f"[{ID}]: Received invalid query params from entry: {queryData[0]}")

                # Oh boy, here I go killing again
                CURSOR.execute(f'SAVEPOINT s{ID}')
                for table, targetList in table_groups:
                    try:
                        execute_batch(CURSOR, UPDATION_SQL.format(tablename=table, ids_to_delete=table_groups[table][0]),
                                    argslist=table_groups[table][1])
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
                        CURSOR.execute(f"ROLLBACK TO s{ID}")
                    except pg.errors.Error as pg_error:
                        print(f"[{ID}]: Error in executing batch delete for table {table}, exception: {pg_error.__class__.__name__}")
                        print(f"[{ID}]: Error details: {format_exc()}")
                        CURSOR.execute(f"ROLLBACK TO SAVEPOINT s{ID}")

                table_groups.clear()
                interface.xtrim(streamName, minid=trimUB)