''' #### Script for perfoming deleion on weak entities pusehd to a Redis stream'''
import os
from dotenv import load_dotenv

from redis import Redis
from redis import Redis, exceptions as redisExceptions

import psycopg2 as pg
from psycopg2.extras import execute_values, execute_batch

from typing import Any
import json
from time import sleep
from traceback import format_exc

from worker_utils import  getDtypes

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

if __name__ == "__main__":
    dtypes_cache: dict = {}
    pk_columns_cache: dict = {}

    # Load basic config
    with open(os.path.join(os.path.dirname(__file__), "worker_config.json"), 'rb') as configFile:
        configData: dict = json.loads(configFile.read())
        wait: float = configData.get("wait", 1)
        backoffSequence: list[float] = configData.get("backoff_seq", [0.1, 0.5, 1, 2, 3])
        backoffIndex, maxBackoffIndex = 0, len(backoffSequence) - 1
        streamName: str = configData["weak_delete_stream"]
        batchSize: int = configData["weak_delete_batch_size"]
    
    
    DELETION_SQL: str = "DELETE FROM {table_name} WHERE {pk_columns_template};"
    
    query_groups: dict[str, list[dict[str, Any]]] = {}       # dict[<tablename> : argslist[dict[<attribute> : <value>]]]
    dbCursor: pg.extensions.cursor = CONNECTION.cursor()

    with dbCursor as dbCursor:
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
            
            for queryData in _streamd_queries:
                try:
                    tableData = {k : None if v == '' else v for k,v in queryData[1].items()}
                    table: str = tableData.pop('table')
                    if table in dtypes_cache:
                        tableData = {k : dtypes_cache[table][idx](v) if v else v for idx, (k,v) in enumerate(tableData.items())}
                    else:
                        dTypesList = getDtypes(dbCursor, table, includePrimaryKey=True)
                        dtypes_cache[table] = dTypesList

                        tableData = {k : dTypesList[idx](v) if v else v for idx, (k,v) in enumerate(tableData.items())}

                    if query_groups.get(table):
                        query_groups[table].append(tableData)
                    else:
                        query_groups[table] = [tableData]
                except KeyError:
                    print(f"[{ID}]: Received invalid query params from entry: {queryData[0]}")
            
            dbCursor.execute(f"SAVEPOINT s{ID}")
            for qidx, (table, qargs) in enumerate(query_groups.items()):
                try:
                    pk_cols: tuple[Any] = pk_columns_cache.get(table)
                    if not pk_cols:
                        pk_cols = pk_columns_cache[table] = tuple(qargs[0].keys())
                    pk_cols_template = ' AND '.join(f"{k} = %({k})s" for k in pk_cols)
                    print(pk_cols_template)
                    execute_batch(cur=dbCursor,
                                  sql=DELETION_SQL.format(table_name = table, pk_columns_template = pk_cols_template),
                                  argslist=qargs,
                                  page_size=batchSize)

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
                    dbCursor.execute(f"ROLLBACK TO s{ID}")
                except pg.errors.Error as pg_error:
                    print(f"[{ID}]: Error in executing batch isnert for table {table}, exception: {pg_error.__class__.__name__}")
                    print(f"[{ID}]: Error details: {format_exc()}")
                    dbCursor.execute(f"ROLLBACK TO SAVEPOINT s{ID}")


            interface.xtrim(streamName, minid=trimUB)
            query_groups.clear()
            # Good night >:3
            sleep(wait)