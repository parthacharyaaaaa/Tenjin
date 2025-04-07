''' #### Script for perfoming deleion on strong entities'''
import os
from dotenv import load_dotenv

import psycopg2 as pg

import json
from time import sleep
from traceback import format_exc

from worker_utils import fetchDeletions

loaded = load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"))
if not loaded:
    raise FileNotFoundError()

ID: int = os.getpid()

STRONG_ENTITIES: list[str] = ["users", "forums", "posts", "comments"] #TODO: Add logic to dynamically fetch strong entities (Independent PK)\

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
    # Load basic config
    with open(os.path.join(os.path.dirname(__file__), "worker_config.json"), 'rb') as configFile:
        configData: dict = json.loads(configFile.read())
        wait: float = configData.get("wait", 5)
        batchSize: int = configData["delete_batch_size"]
    
    
    DELETION_SQL: str = "DELETE FROM {table_name} WHERE id IN ({ids_to_delete});"
    dbCursor: pg.extensions.cursor = CONNECTION.cursor()

    with dbCursor as dbCursor:
        while(True):
            for table in STRONG_ENTITIES:
                hitlist = fetchDeletions(dbCursor, table)
                if not hitlist:
                    continue

                dbCursor.execute(f"SAVEPOINT s{ID}")
                try:
                    dbCursor.execute(query=DELETION_SQL.format(table_name = table, ids_to_delete = ', '.join(hitlist)))

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
                    print(f"[{ID}]: Error in executing batch delete for table {table}, exception: {pg_error.__class__.__name__}")
                    print(f"[{ID}]: Error details: {format_exc()}")
                    dbCursor.execute(f"ROLLBACK TO SAVEPOINT s{ID}")

            # Good night >:3
            sleep(wait)