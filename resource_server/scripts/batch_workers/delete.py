from redis import Redis
import psycopg2 as pg
from psycopg2.extras import execute_values, execute_batch
from dotenv import load_dotenv
import os
import time
from typing import Any
from traceback import format_exc
import json

def fetchPKColNames(cursor: pg.extensions.cursor, tableName: str) -> list[str]:
    cursor.execute('''
                    SELECT
                    kcu.column_name AS key_column
                    FROM information_schema.table_constraints tco
                    JOIN information_schema.key_column_usage kcu 
                    ON kcu.constraint_name = tco.constraint_name
                    AND kcu.constraint_schema = tco.constraint_schema
                    WHERE tco.constraint_type = 'PRIMARY KEY'
                    AND tco.table_schema = 'public'
                    AND kcu.table_name = %s
                    ORDER BY kcu.ordinal_position;''', 
                    (tableName,))
    return [str(res[0]) for res in cursor.fetchall()]

loaded = load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"))
if not loaded:
    raise FileNotFoundError()

ID: int = os.getpid()

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
    tempTable: str = f"deletion_table_{ID}_{str(int(time.time()))}"
    with open(os.path.join(os.path.dirname(__file__), "worker_config.json"), 'rb') as configFile:
        configData: dict = json.loads(configFile.read())
        wait: float = configData.get("wait", 1)
        backoffSequence: list[float] = configData.get("backoff_seq", [0.1, 0.5, 1, 2, 3])
    

    DELETION_SQL: str = "DELETE FROM {table_name} USING {temp_del_table} WHERE {clause}"
    ERROR_SQL: str = "INSERT INTO delete_errors VALUES %s ON CONFLICT DO NOTHING;"
    query_groups: dict[str, list[tuple[int]]] = {}       # dict[<tablename> : list[tuple[PK values in <tablename>]]]
    dbCursor: pg.extensions.cursor = CONNECTION.cursor()

    # Some logic to xread 1k from insert consumer group
    # some logic to sort queries into respective buckets
    with dbCursor as dbCursor:
        for table, pkargs in query_groups.items():
            # Create temp table 
            # For deletion, we'll need to dynamically create the comparison logic by fetching the PK column names of each table
            pk_cols: list[str] = fetchPKColNames(dbCursor, table)

            DELETION_WHERE_CLAUSE: str = " AND ".join(f"{table}.{col} = {tempTable}.{col}" for col in pk_cols)

            dbCursor.execute(f"CREATE TABLE {tempTable} AS SELECT {', '.join([col for col in pk_cols])} FROM {table} WHERE 1 = 0 LIMIT 1;")

            execute_batch(cur=dbCursor,
                          sql=f"INSERT INTO {tempTable} VALUES %s",
                          argslist=pkargs)
            CONNECTION.commit()

            dbCursor.execute(DELETION_SQL.format(table_name = table, 
                                                 temp_del_table = tempTable,
                                                 clause=DELETION_WHERE_CLAUSE))
            dbCursor.execute(f"DROP TABLE {tempTable}")

            CONNECTION.commit()