"""SQL templates and composed strings"""

from datetime import datetime
from typing import Final, Literal, Sequence

from psycopg.sql import SQL, Composed, Identifier, Literal as SQL_Literal

from resource_auxillary.datastructures.database import (
    EventLiteral,
    EventMetadataLiteral,
)

SINGLE_DEDUP_STATEMENT: Final[SQL] = SQL("""INSERT INTO {event_dedup_table}
    ({event_id_col}, {ack_time_col})
    VALUES ({event_id}, {acknowledgement_time})""")


def prepare_single_dedup_sql(
    event_id: int, acknowledgement_time: datetime | None = None
) -> Composed:
    return SINGLE_DEDUP_STATEMENT.format(
        event_dedup_table=Identifier(EventLiteral.EVENTS_TABLE_NAME),
        event_id_col=Identifier(EventLiteral.EVENT_ID_COLUMN_NAME),
        ack_time_col=Identifier(EventLiteral.EVENT_TIMESTAMP_COLUMN_NAME),
        event_id=SQL_Literal(event_id),
        acknowledgement_time=SQL_Literal(acknowledgement_time or datetime.now()),
    )


BATCH_DEDUP_STATEMENT: Final[SQL] = SQL("""WITH attempted AS (
        INSERT INTO {event_dedup_table} ({event_id_col}, {ack_time_col})
        SELECT {event_id_col}, {ack_time_col}
        FROM {temp_table}
        ON CONFLICT ({event_id_col}) DO NOTHING
        RETURNING {event_id_col}
    )
    SELECT {event_id_col} FROM attempted;""")


def prepare_batch_dedup_sql(temp_table: str) -> Composed:
    return SINGLE_DEDUP_STATEMENT.format(
        event_dedup_table=Identifier(EventLiteral.EVENTS_TABLE_NAME),
        event_id_col=Identifier(EventLiteral.EVENT_ID_COLUMN_NAME),
        ack_time_col=Identifier(EventLiteral.EVENT_TIMESTAMP_COLUMN_NAME),
        temp_table=Identifier(temp_table),
    )


TEMP_TABLE_SQL: Final[SQL] = SQL("""CREATE TEMP TABLE {table}
                                 (LIKE {reference} INCLUDING DEFAULTS)
                                 ON COMMIT DROP;""")


def prepare_temp_table_sql(tablename: str, reference_table: str) -> Composed:
    return TEMP_TABLE_SQL.format(
        table=Identifier(tablename), reference=Identifier(reference_table)
    )


WEAK_INSERTION_COPY_SQL: Final[SQL] = SQL("""COPY {table}
                                          ({columns})
                                          FROM STDIN;""")


def prepare_weak_insertion_copy_sql(table: str, *columns: str) -> Composed:
    return WEAK_INSERTION_COPY_SQL.format(
        table=Identifier(table), columns=SQL(", ").join(Identifier(c) for c in columns)
    )


WEAK_INSERTION_SQL: Final[SQL] = SQL(
    """INSERT INTO {table} AS insertion_table ({columns})
    SELECT {columns}
    FROM {temp_table}
    ON CONFLICT ({conflict_columns})
    DO UPDATE SET
    {state_column} = EXCLUDED.{state_column},
    {event_seq_column} = EXCLUDED.{event_seq_column}
    WHERE {event_seq_column} < EXCLUDED.{event_seq_column}
    RETURNING insertion_table.{event_id_column};"""
)


def prepare_weak_insertion_sql(
    table: str,
    temp_table: str,
    columns: Sequence[str],
    conflicting_columns: Sequence[str],
    action: Literal["save", "vote", "subscribe"],
) -> Composed:
    if action == "save":
        state_column = EventMetadataLiteral.EVENT_SAVE_COLUMN_NAME
    elif action == "vote":
        state_column = EventMetadataLiteral.EVENT_VOTE_COLUMN_NAME
    else:
        state_column = EventMetadataLiteral.EVENT_SUB_COLUMN_NAME

    return WEAK_INSERTION_SQL.format(
        table=Identifier(table),
        columns=SQL(", ").join(map(Identifier, columns)),
        temp_table=Identifier(temp_table),
        state_column=Identifier(state_column),
        event_seq_column=Identifier(
            EventMetadataLiteral.LAST_EVENT_IDENTIFIER_COLUMN_NAME
        ),
        conflict_columns=SQL(", ").join(Identifier(c) for c in conflicting_columns),
    )
