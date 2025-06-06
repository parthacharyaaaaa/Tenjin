import psycopg2 as pg
from psycopg2.extras import execute_values
from redis import Redis
from redis import Redis, exceptions as redisExceptions
from resource_server.scripts.batch_workers.worker_utils import getDtypes
import os
from dotenv import load_dotenv
from typing import Any
import json
from time import sleep
from traceback import format_exc



if __name__ == "__main__":
    loaded = load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"))
    if not loaded:
        raise FileNotFoundError()

    ID: int = os.getpid()

    interface: Redis = Redis(os.environ["REDIS_HOST"], int(os.environ["REDIS_PORT"]), decode_responses=True)

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
    
    # Initialize empty caches for data types for insertions and templates
    dtypes_cache: dict[str, list[type]] = {}
    templates_cache: dict[str, str] = {}
    query_groups: dict[str, list[dict[str, Any]]] = {}       # dict[<tablename> : argslist[dict[<attribute> : <value>]]]

    # Initialize configurations for this worker
    with open(os.path.join(os.path.dirname(__file__), "worker_config.json"), 'rb') as configFile:
        configData: dict = json.loads(configFile.read())
        wait: float = configData.get("wait", 1)
        backoffSequence: list[float] = configData.get("backoff_seq", [0.1, 0.5, 1, 2, 3])
        backoffIndex, maxBackoffIndex = 0, len(backoffSequence) - 1
        streamName: str = configData["weak_insert_stream"]
        batchSize: int = configData["weak_insert_batch_size"]
    
    # Prepare SQL template
    INSERTION_SQL: str = "INSERT INTO {table_name} {tColumns} VALUES %s ON CONFLICT DO NOTHING;"
    with CONNECTION.cursor() as dbCursor:
        while(True):
            try:
                _streamd_queries: list[tuple[str, dict[str, str]]] = interface.xrange(streamName, count=batchSize)
            except (redisExceptions.ConnectionError):
                # For connection errors, try retrying at increasing backoff periods
                if backoffIndex >= maxBackoffIndex:
                    print(f"[{ID}]: Connection to Redis instance compromised, exiting...")
                    exit(100)
                sleep(backoffSequence[backoffIndex])
                backoffIndex+=1
                continue
            
            backoffIndex = 0    # Reset backoff index upon succesfull network request cycle
            if not _streamd_queries:
                sleep(wait)
                continue

            # Remember upper bound of fetched substream for trimming later
            trimUBs: str = _streamd_queries[-1][0].split("-")
            trimUB: str = '-'.join((trimUBs[0], str(int(trimUBs[1]) + 1)))
            
            for query_data in _streamd_queries:
                try:
                    table_data = {k : None if v == '' else v for k,v in query_data[1].items()}
                    table: str = table_data.pop('table')    # Remove and read helper field 'table'
                    if table not in dtypes_cache:
                        dTypesList = getDtypes(dbCursor, table, includePrimaryKey=True)     # Discriminators for weak entities are provided in streams as they are PKs for assosciated strong entities
                        dtypes_cache[table] = dTypesList

                    # Prepare Python/psycopg2 compatible mapping for this entry
                    table_data = {k : dtypes_cache[table][idx](v) if v else v for idx, (k,v) in enumerate(table_data.items())}

                    if query_groups.get(table):
                        query_groups[table].append(table_data)
                    else:
                        query_groups[table] = [table_data]
                except KeyError:
                    print(f"[{ID}]: Received invalid query params from entry: {query_data[0]}")
            
            dbCursor.execute(f"SAVEPOINT s{ID}")
            for table, qargs in query_groups.items():
                columns = tuple(qargs[0].keys())
                tColumns = '(' + ', '.join(columns) + ')'
                # Fetch argslist template for this table from cache, else compute and cache
                template: str =  templates_cache.get(table)
                if not template:
                    template: str = '(' + ', '.join(f"%({k})s" for k in columns) + ')'
                    templates_cache[table] = template
                try:
                    execute_values(cur=dbCursor, 
                                   sql=INSERTION_SQL.format(table_name = table, tColumns = tColumns),
                                   argslist=qargs, template=template)
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
                    print(f"[{ID}]: Error in executing batch insert for table {table}, exception: {pg_error.__class__.__name__}")
                    print(f"[{ID}]: Error details: {format_exc()}")
                    print("Current vars:")
                    print(templates_cache)
                    print(INSERTION_SQL.format(table_name = table, tColumns = tColumns))
                    print(qargs)
                    print(template)
                    dbCursor.execute(f"ROLLBACK TO SAVEPOINT s{ID}")

            interface.xtrim(streamName, minid=trimUB)   # Finally trim consumed substream
            query_groups.clear()
            # Good night >:3
            sleep(wait)