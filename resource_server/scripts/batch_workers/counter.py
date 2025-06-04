'''UPDATION consumer, but only for counters (votes, saves, reports, followers, etc.)
'''
import os
from dotenv import load_dotenv

from redis import Redis
from redis import Redis, exceptions as redisExceptions

import psycopg2 as pg
from psycopg2.extras import execute_values, execute_batch

from time import sleep
import json
from traceback import format_exc


if __name__ == "__main__":
    UPDATION_SQL: str = "UPDATE {table_name} SET {column_name} = %(counter)s WHERE id = %(id)s;"
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
    
    sleep_duration: int = 5
    # Counters will be accessible through hashmaps, not streams like other scripts
    counter_hashmap_names: set[str] = set()
    with open(os.path.join(os.path.dirname(__file__), "worker_config.json"), 'rb') as config_file:
        # Fetch resources and column names for which counters exist. key 'counter_metadata' will contain the mapping needed
        counter_metadata: dict[str, list[str]] = json.loads(config_file.read()).get('counter_metadata')
        assert counter_metadata, "Config file for batch workers missing mandatory field: counter_metadata"

        # Prepare collection of hashmap names to query from Redis
        for resource, fields in counter_metadata:
            for field in fields:
                # Hashmaps for counters follow the convention: table:column, example: users:total_posts, posts:score
                counter_hashmap_names.add(f'{resource}:{field}')
        
    
    with CONNECTION.cursor() as CURSOR:
        while(True):
            for counter_mapping in counter_hashmap_names:
                table, column = counter_mapping.split(":")
                # For each hashmap name prepared, atomically fetch and clear the hashmap
                with interface.pipeline() as pipe:
                    pipe.hgetall(counter_mapping)
                    pipe.delete(counter_mapping)
                    _res: tuple[dict[str, str], int] = pipe.execute()

                if not _res[0]:
                    continue
                counter_data: list[dict[str, int]] = [{'id' : key, 'counter' : counter} for key, counter in _res[0].items()]    # argslist argument
                
                try:
                    execute_batch(cur=CURSOR,
                                  sql=UPDATION_SQL.format(table_name=table, column_name=column),
                                  argslist=counter_data,
                                  page_size=len(counter_data))
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
                    print(f"[{ID}]: Error in executing batch isnert for table {table}, exception: {pg_error.__class__.__name__}")
                    print(f"[{ID}]: Error details: {format_exc()}")

                sleep(sleep_duration)