from redis import Redis
import psycopg2 as pg
from psycopg2.extras import execute_values, execute_batch
from dotenv import load_dotenv
import os
from datetime import datetime
from typing import Any
from traceback import format_exc
import json

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
    with open(os.path.join(os.path.dirname(__file__), "worker_config.json"), 'rb') as configFile:
        configData: dict = json.loads(configFile.read())
        wait: float = configData.get("wait", 1)
        backoffSequence: list[float] = configData.get("backoff_seq", [0.1, 0.5, 1, 2, 3])
    

    INSERTION_SQL: str = "INSERT INTO {table_name} VALUES %s ON CONFLICT DO NOTHING RETURNING {query_id};"
    ERROR_SQL: str = "INSERT INTO insert_errors VALUES %s ON CONFLICT DO NOTHING;"
    query_groups: dict[str, list[dict[str, Any]]] = {}       # Please, I can explain dict[<tablename> : argslist[dict[<attribute> : <value>]]]
    dbCursor: pg.extensions.cursor = CONNECTION.cursor()

    # Some logic to xread 1k from insert consumer group
    # some logic to sort queries into respective buckets
    with dbCursor as dbCursor:
        for qidx, (table, qargs) in enumerate(query_groups.items()):
            template: str =  f"({', '.join(qargs[0].keys())})"
            _res: tuple[tuple[int]] = execute_values(cur=dbCursor, 
                                                     sql=INSERTION_SQL.format(table_name = table, query_id = qidx),
                                                     argslist=qargs,
                                                     template=template,
                                                     fetch=True)
            if _res:
                execute_batch(cur=dbCursor,
                              sql=ERROR_SQL,
                              argslist=[t[0] for t in _res])
