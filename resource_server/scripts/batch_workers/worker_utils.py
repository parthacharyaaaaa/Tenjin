'''Auxillary functions for batch workers'''
import psycopg2 as pg
from typing import Mapping
from types import MappingProxyType

# We got reinvented SQLAlchemy before GTA VI
MAPPED_DTYPES: MappingProxyType[str, type] = MappingProxyType({'integer': int,
                                                               'smallint': int,
                                                               'bigint': int,
                                                               'numeric': float,
                                                               'double precision': float,
                                                               'character varying': str,
                                                               'character': str,
                                                               'text': str,  
                                                               'bytea': bytes,
                                                               'timestamp without time zone': str,
                                                               'timestamp with time zone': str,
                                                               'date': str,
                                                               'time without time zone': str,
                                                               'time with time zone': str,
                                                               'boolean': lambda val : bool(int(val)),
                                                               'json': str,
                                                               'jsonb': str,
                                                               'uuid': str,
                                                               'inet': str
                                                            })


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

def derediserialize(mapping: Mapping, typeMapping: dict = {'' : None}) -> Mapping:
    '''Deserialize a Redis hashmap to its original Python mapping, compatible with Postgres'''
    return {k : None if v == '' else v for k,v in mapping.items()}

def getDtypes(cursor: pg.extensions.cursor, table: str, includePrimaryKey: bool = False) -> list[type]:
    '''Return ordered list of a table's column data types'''
    if includePrimaryKey:
        cursor.execute("SELECT data_type FROM information_schema.columns WHERE table_name = %s",(table,))
    else:
        cursor.execute('''SELECT c.data_type 
                       FROM information_schema.columns c
                       WHERE c.table_name = %s
                       AND c.column_name NOT IN 
                       (SELECT a.attname FROM pg_index i 
                       JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                       JOIN pg_class t ON t.oid = i.indrelid
                       WHERE i.indisprimary AND t.relname = %s);''', (table, table))
    return [MAPPED_DTYPES.get(x[0], str) for x in cursor.fetchall()]

def fetchDeletions(cursor: pg.extensions.cursor, table: str, castStr: bool = True):
    '''Fetch flagged rows from a given table, returing their primary key'''
    cursor.execute(f"SELECT id FROM {table} WHERE deleted = true FOR UPDATE SKIP LOCKED;")
    result = cursor.fetchall()
    if not result:
        return []
    
    return [str(pk[0]) for pk in result] if castStr else [pk[0] for pk in result]
