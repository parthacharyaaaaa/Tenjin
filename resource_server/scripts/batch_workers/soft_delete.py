'''Perform soft deletion on records.
Tables affected:
- posts
- comments
- forums

Works on the assumption that a soft deletion is represented as a true value in the `deleted` column, accompanied with a TIMESTAMP value in `time_deleted
`'''
import psycopg2 as pg
from psycopg2.extras import execute_batch
from redis import Redis
from redis import exceptions as redisExceptions
from resource_server.scripts.batch_workers.worker_utils import enqueue_cascade_soft_deletes
import os
from dotenv import load_dotenv
from time import sleep
from datetime import datetime
from traceback import format_exc
from typing import Any
import json
import toml

if __name__ == '__main__':
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

    CONNECTION: pg.extensions.connection = pg.connect(**CONNECTION_KWARGS)
    DEPENDENT_MAPPING: dict[str, dict[str, str]] = {'forums' : {'posts' : 'parent_forum'},
                                                    'posts' : {'comments' : 'parent_post'}}
    # Initialize updation SQL
    UPDATION_SQL: str = "UPDATE {tablename} SET time_deleted = %s, deleted = true WHERE id IN ({ids_to_delete});"

    # Initalize configuration for this worker
    with open(os.path.join(os.path.dirname(__file__), "worker_config.json"), 'rb') as configFile:
        configData: dict = json.loads(configFile.read())
        wait: float = configData.get("wait", 1)
        backoffSequence: list[float] = configData.get("backoff_seq", [0.1, 0.5, 1, 2, 3])
        backoffIndex, maxBackoffIndex = 0, len(backoffSequence) - 1
        streamName: str = configData["soft_delete_stream"]
        batchSize: int = configData["soft_delete_batch_size"]
    
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
                    sleep(wait)
                    backoffIndex+=1
                    continue

                backoffIndex = 0    # Reset backoff index on succesfull network call
                if not _streamd_queries:
                    sleep(wait)
                    continue

                trimUBs: str = _streamd_queries[-1][0].split("-")
                trimUB: str = '-'.join((trimUBs[0], str(int(trimUBs[1]) + 1)))

                # Stream entries will contain unique ID, table name, and primary key of target record
                table_groups: dict[str, list[list[int], list[datetime]]] = {}   # {<tablename> : [<ordered list of IDs to delete>, <ordered list of datetime objects for 'time_deleted>' column]}
                for queryData in _streamd_queries:
                    # Prepare table groups with mappings containing table names as keys and list of 2 lists, with IDs to delete and their deleted timestamps as pairs at equal indices
                    try:
                        table: str = queryData[1].pop('table')
                        if table not in table_groups:
                            table_groups[table] = [[], []]
                        
                        timestamp: float = float(queryData[0].split("-")[0]) / 1000
                        time_deleted: datetime = datetime.fromtimestamp(timestamp)

                        streamed_id: int = int(queryData[1].pop('id'))
                        # Add to table groups
                        table_groups[table][0].append(streamed_id)
                        table_groups[table][1].append(time_deleted)

                    except KeyError:
                        print(f"[{ID}]: Received invalid query params from entry: {queryData[0]}")
                        print(format_exc())

                # All fetched records now arranged table-wise
                CURSOR.execute(f'SAVEPOINT s{ID}')
                for table, targetList in table_groups.items():
                    # Oh boy, here I go killing again
                    try:
                        execute_batch(CURSOR, UPDATION_SQL.format(tablename=table, ids_to_delete=table_groups[table][0]),
                                    argslist=[table_groups[table][1]])
                        CONNECTION.commit()

                        # And not just the men, but the women and the children too!
                        if table in DEPENDENT_MAPPING:
                            for dependent_table, dependent_fk in DEPENDENT_MAPPING[table].items():
                                enqueue_cascade_soft_deletes(CURSOR, interface, target_table=dependent_table, fk_colname=dependent_fk, parent_pk_seq=table_groups[table][0], stream_name=streamName)
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
                sleep(wait)