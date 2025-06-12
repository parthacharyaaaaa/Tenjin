import psycopg2 as pg
from psycopg2.extras import execute_values
from redis import Redis
from redis import Redis, exceptions as redisExceptions
from resource_server.scripts.batch_workers.worker_utils import getDtypes
import os
from dotenv import load_dotenv
from typing import Any
from time import sleep
from traceback import format_exc
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
    
    # Initialize empty caches for data types for insertions and templates
    dtypes_cache: dict[str, list[type]] = {}
    templates_cache: dict[str, str] = {}
    pk_cache: dict[str, int] = {}
    query_groups: dict[str, list[dict[str, Any]]] = {}       # dict[<tablename> : argslist[dict[<attribute> : <value>]]]
    flag_name_template: str = '{resource}:{identifiers}'
    flag_names_mapping: dict[str, list[str]] = {}

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
                        dTypesList: list[type] = getDtypes(dbCursor, table, includePrimaryKey=True)     # Discriminators for weak entities are provided in streams as they are PKs for assosciated strong entities
                        pk_dtypes: int = len(dTypesList) - len(getDtypes(dbCursor, table, includePrimaryKey=False))
                        dtypes_cache[table] = dTypesList
                        pk_cache[table] = pk_dtypes

                    # Format and append the goddamn string in the goddamn fucking goddamn
                    if table not in flag_names_mapping:
                        flag_names_mapping[table] = []
                    
                    flag_names_mapping[table].append(flag_name_template.format(resource=table, identifiers=':'.join(list(table_data.values())[:pk_cache[table]])))
                    # identifiers would be colon-separated, ordered discriminators. Slicing is important here in case of additional attributes to the weak entity, such as 'vote' in post_votes and comment_votes
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

                # Clear all flags, irrespective of succeess or failure (to allow retries)
                interface.delete(*flag_names_mapping[table])
                flag_names_mapping.pop(table)

            interface.xtrim(streamName, minid=trimUB)   # Finally trim consumed substream
            query_groups.clear()
            # Good night >:3
            sleep(wait)