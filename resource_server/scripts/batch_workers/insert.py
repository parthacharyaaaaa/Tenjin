import psycopg2 as pg
from psycopg2.extras import execute_values
from redis import Redis
from redis import Redis, exceptions as redisExceptions
from resource_server.scripts.batch_workers.worker_utils import MAPPED_DTYPES, batch_cache_write, get_column_types
from auxillary.utils import rediserialize
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

    del CONNECTION_KWARGS
    # Initialize empty caches
    dtypes_cache: dict = {}
    tables_cache: dict[str, dict[str, str]] = {}    # Keys are table names, values are dictionaries with templates, and typed columns

    # Initialize mapping to group insertion queries by tables
    query_groups: dict[str, list[dict[str, Any]]] = {}       # dict[<tablename> : argslist[dict[<attribute> : <value>]]]

    # Initialize configurations for this worker
    with open(os.path.join(os.path.dirname(__file__), "worker_config.json"), 'rb') as configFile:
        configData: dict = json.loads(configFile.read())
        wait: float = configData.get("wait", 1)
        backoffSequence: list[float] = configData.get("backoff_seq", [0.1, 0.5, 1, 2, 3])
        backoffIndex, maxBackoffIndex = 0, len(backoffSequence) - 1
        streamName: str = configData["insert_stream"]
        batchSize: int = configData["insert_batch_size"]
    
    # Initialize SQL template 
    # For cache write-back, getting an ID is not enough since we need to know which mapping in qargs the ID belongs to (A failed insert will not return anything, and hence the length of the result from execute_values() can be shorter than the length of the fetched batch).
    # For this, we will inject a fake column '_python_enumeration_index' in a CTE, and use another CTE to project only the real schema columns.
    # Lastly, after insertion through this 2nd CTE, we perform a JOIN in memory itself between the 2 CTEs to finally get tuples containing (database_id, enumeration_index) pairs of flushed records only
    INSERTION_SQL: str = '''
      WITH input_query_data ({table_columns}, _python_enumeration_index) AS (
        VALUES %s
      ),
      inserted_query_data AS (
        INSERT INTO {table_name} ({table_columns})
        SELECT {typed_table_columns} FROM input_query_data
        ON CONFLICT DO NOTHING
        RETURNING id, {table_columns}
      )
      SELECT inserted_query_data.id, input_query_data._python_enumeration_index
      FROM inserted_query_data
      INNER JOIN input_query_data ON
      {join_clauses}
      '''
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
            
            backoffIndex = 0    # Reset upon success
            if not _streamd_queries:
                sleep(wait)
                continue
            
            # Determine last entry in fetched records for trimming later
            trimUBs: str = _streamd_queries[-1][0].split("-")
            trimUB: str = '-'.join((trimUBs[0], str(int(trimUBs[1]) + 1)))
            
            for query_data in _streamd_queries:
                try:
                    table_data = {k : None if v == '' else v for k,v in query_data[1].items()}
                    table: str = table_data.pop('table') # Remove helper field 'table' from table_data

                    # Cast data types back to Python/psycopg2 compatible types
                    if table not in dtypes_cache:
                        # Fetch and cache list of data types for this table if not cached already
                        dtypes_cache[table] = [MAPPED_DTYPES.get(dtype, str) for dtype in get_column_types(dbCursor, table)]

                    # Finally cast to Python data types compatible with psycopg2
                    table_data = {k : dtypes_cache[table][idx](v) if v else v for idx, (k,v) in enumerate(table_data.items())}

                    # Append mapping to list with key as table name
                    if query_groups.get(table):
                        query_groups[table].append(table_data)
                    else:
                        query_groups[table] = [table_data]
                except KeyError:
                    print(f"[{ID}]: Received invalid query params from entry: {query_data[0]}")
            
            dbCursor.execute(f"SAVEPOINT s{ID}")
            for table, qargs in query_groups.items():
                try:
                    # Resolve column names string to inject into insertion query (NOTE: Columns must never include the enumeration index, because it is not part of the schema)
                    columns = tuple(qargs[0].keys())
                    tColumns = ', '.join(columns)

                    # Fetch query template from cache, or resolve and cache
                    cached_data: dict[str, str] = tables_cache.get(table)
                    if cached_data:
                        template: str = cached_data['template']
                        typed_columns: str = cached_data['typed_columns']
                        join_clauses: str = cached_data['join_clauses']
                    else:
                        template: str = '(' + ', '.join(f"%({k})s" for k in (*columns, '_python_enumeration_index')) + ')' # Inject enumeration index into the VALUES list

                        # Prepare typed column names for 2nd CTE
                        column_types: list[str] = get_column_types(dbCursor, table)
                        typed_columns = ','.join(f'{col}::{column_types[idx]}' for idx, col in enumerate(columns))

                        # Prepare join clause for final result
                        join_clauses: str = ' AND '.join(f'''inserted_query_data.{col} = input_query_data.{col}::{column_types[idx]}
                                                         OR (inserted_query_data.{col} IS NULL AND input_query_data.{col}::{column_types[idx]} IS NULL)''' 
                                                         for idx, col in enumerate(columns))    # Since NULL <> NULL in SQL, brilliant >:/
                        tables_cache[table] = {'typed_columns' : typed_columns, 'template' : template, 'join_clauses' : join_clauses}
                        #TODO: Change str to sql data types for better compliance/sanity

                    # Inject _python_enumeration_index columns in argslist directly, this is because qargs is to be used later for caching and its stupid to iterate over it again to remove _python_enumeration_index fields
                    _res: tuple[tuple[int, int]] = execute_values(cur=dbCursor,
                                                                  sql=INSERTION_SQL.format(table_name = table, table_columns = tColumns, typed_table_columns=typed_columns, join_clauses = join_clauses),
                                                                  argslist=[qarg | {'_python_enumeration_index' : qidx} for qidx, qarg in enumerate(qargs)], 
                                                                  template=template,
                                                                  page_size=len(query_groups), fetch=True)
                    CONNECTION.commit()
                    if not _res:
                        sleep(wait)
                        continue

                    # After flushing to DB, perform cache write-through. Naming convention is: resource:identifier, such as posts:<post_id> and comments:<comment_id>
                    cache_entries: dict[str, dict[str, Any]] = {f'{table}:{returned_values[0]}' : {'id' : returned_values[0]} | rediserialize(qargs[returned_values[1]]) for returned_values in _res}

                    # Cache newly inserted entries together
                    batch_cache_write(interface, cache_entries, 300)
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


            interface.xtrim(streamName, minid=trimUB)   # Trim consumed records
            query_groups.clear()
            # Good night >:3
            sleep(wait)