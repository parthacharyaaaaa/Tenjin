"""Auxillary functions for batch workers"""

import os
import toml
from datetime import datetime
from traceback import format_exc
from types import MappingProxyType
from typing import Callable, Mapping, Any

from dotenv import load_dotenv

import psycopg2 as pg
from psycopg2 import sql
from psycopg2.extensions import connection

from redis import Redis

# We got reinvented SQLAlchemy before GTA VI
MAPPED_DTYPES: MappingProxyType[str, Callable] = MappingProxyType(
    {
        "integer": int,
        "smallint": int,
        "bigint": int,
        "numeric": float,
        "double precision": float,
        "character varying": str,
        "character": str,
        "text": str,
        "bytea": bytes,
        "timestamp without time zone": lambda dt: datetime.fromisoformat(dt),
        "timestamp with time zone": lambda dt: datetime.fromisoformat(dt),
        "date": str,
        "time without time zone": str,
        "time with time zone": str,
        "boolean": lambda val: bool(int(val)),
        "json": str,
        "jsonb": str,
        "uuid": str,
        "inet": str,
    }
)


def initialize_environment(worker_id: int) -> tuple[connection, Redis]:
    # loaded = load_dotenv(
    #     os.path.join(
    #         os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"
    #     )
    # )
    # if not loaded:
    #     raise FileNotFoundError()

    redis_config_fpath: str = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "config",
        os.environ["REDIS_CONFIG_FILENAME"],
    )

    if not os.path.isfile(redis_config_fpath):
        raise FileNotFoundError("Redis config toml file not found")

    redis_config_kwargs: dict[str, Any] = toml.load(f=redis_config_fpath)
    redis_config_kwargs.update(
        {
            "username": os.environ["BATCH_SERVER_REDIS_USERNAME"],
            "password": os.environ["BATCH_SERVER_REDIS_PASSWORD"],
        }
    )  # Inject login credentials through env
    redis: Redis = Redis(**redis_config_kwargs)

    CONNECTION_KWARGS: dict[str, int | str] = {
        "user": os.environ["WORKER_POSTGRES_USERNAME"],
        "password": os.environ["WORKER_POSTGRES_PASSWORD"],
        "host": os.environ["RESOURCE_SERVER_POSTGRES_HOST"],
        "port": int(os.environ["RESOURCE_SERVER_POSTGRES_PORT"]),
        "database": os.environ["RESOURCE_SERVER_POSTGRES_DATABASE"],
    }

    try:
        conn: connection = pg.connect(**CONNECTION_KWARGS)
    except Exception as e:
        print(
            f"{worker_id}: Failed to connect to Postgres instance.\n\tError: {e.__class__.__name__}\n\tError Logs: ",
            format_exc(),
        )
        raise SystemExit(1)

    return conn, redis

def fetchPKColNames(cursor: pg.extensions.cursor, tableName: str) -> list[str]:
    cursor.execute(
        """
                    SELECT
                    kcu.column_name AS key_column
                    FROM information_schema.table_constraints tco
                    JOIN information_schema.key_column_usage kcu 
                    ON kcu.constraint_name = tco.constraint_name
                    AND kcu.constraint_schema = tco.constraint_schema
                    WHERE tco.constraint_type = 'PRIMARY KEY'
                    AND tco.table_schema = 'public'
                    AND kcu.table_name = %s
                    ORDER BY kcu.ordinal_position;""",
        (tableName,),
    )
    return [str(res[0]) for res in cursor.fetchall()]


def derediserialize(mapping: Mapping, typeMapping: dict = {"": None}) -> Mapping:
    """Deserialize a Redis hashmap to its original Python mapping, compatible with Postgres"""
    return {k: None if v == "" else v for k, v in mapping.items()}


def getDtypes(
    cursor: pg.extensions.cursor, table: str, includePrimaryKey: bool = False
) -> list[type]:
    """Return ordered list of a table's column data types"""
    if includePrimaryKey:
        cursor.execute(
            "SELECT data_type FROM information_schema.columns WHERE table_name = %s",
            (table,),
        )
    else:
        cursor.execute(
            """SELECT c.data_type 
                       FROM information_schema.columns c
                       WHERE c.table_name = %s
                       AND c.column_name NOT IN 
                       (SELECT a.attname FROM pg_index i 
                       JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                       JOIN pg_class t ON t.oid = i.indrelid
                       WHERE i.indisprimary AND t.relname = %s);""",
            (table, table),
        )
    return [MAPPED_DTYPES.get(x[0], str) for x in cursor.fetchall()]


def get_column_types(
    cursor: pg.extensions.cursor, table: str, includePrimaryKey: bool = False
) -> list[type]:
    """Return ordered list of a table's column data types"""
    if includePrimaryKey:
        cursor.execute(
            "SELECT data_type FROM information_schema.columns WHERE table_name = %s",
            (table,),
        )
    else:
        cursor.execute(
            """SELECT c.data_type 
                       FROM information_schema.columns c
                       WHERE c.table_name = %s
                       AND c.column_name NOT IN 
                       (SELECT a.attname FROM pg_index i 
                       JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                       JOIN pg_class t ON t.oid = i.indrelid
                       WHERE i.indisprimary AND t.relname = %s);""",
            (table, table),
        )
    return [x[0] for x in cursor.fetchall()]


def fetchDeletions(cursor: pg.extensions.cursor, table: str, castStr: bool = True):
    """Fetch flagged rows from a given table, returing their primary key"""
    cursor.execute(
        f"SELECT id FROM {table} WHERE deleted = true FOR UPDATE SKIP LOCKED;"
    )
    result = cursor.fetchall()
    if not result:
        return []

    return [str(pk[0]) for pk in result] if castStr else [pk[0] for pk in result]


def batch_cache_write(
    interface: Redis,
    cache_entries: Mapping[str, Mapping[str, Any]],
    ttl: int,
    transaction: bool = False,
) -> None:
    """
    Perform a batch write into cache with given mappings in a single network round trip
    Args:
        interface: Redis instance connected to cache server
        cache_entries: Mapping of cache entries, where key is the name of the hashmap and correspoding key is the actual cache hashmap
        ttl: TTL in seconds to assign to each cache entry
        transaction: Whether to execute all cache writes atomically, Defaults to False to avoid overhead
    """
    with interface.pipeline(transaction=transaction) as pipe:
        for name, entry in cache_entries.items():
            pipe.hset(name, mapping=entry)
            pipe.expire(name, ttl)
        pipe.execute()


def enqueue_cascade_soft_deletes(
    cursor: pg.extensions.cursor,
    client: Redis,
    target_table: str,
    fk_colname: str,
    parent_pk_seq: list[int],
    stream_name: str = "SOFT_DELETIONS",
) -> None:
    query: sql.SQL = sql.SQL("SELECT id FROM {} WHERE {} = ANY(%s);").format(
        sql.Identifier(target_table), sql.Identifier(fk_colname)
    )
    cursor.execute(query, (parent_pk_seq,))
    children_ids: list[int] = [row_tuple[0] for row_tuple in cursor.fetchall()]

    with client.pipeline(transaction=False) as pipe:
        for child_id in children_ids:
            pipe.xadd(name=stream_name, fields={"table": target_table, "id": child_id})
            pipe.execute()
