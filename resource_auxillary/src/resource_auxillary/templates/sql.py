"""SQL templates and composed strings"""

from resource_auxillary.datastructures.database import SideEffectsLiteral
from resource_auxillary.datastructures.database import SideEffectsTables
from datetime import datetime
from typing import Final, Literal, Sequence

from psycopg.sql import SQL, Composed, Identifier, Literal as SQL_Literal

from resource_auxillary.datastructures.database import (
    EventLiteral,
    EventMetadataLiteral,
)
from resource_auxillary.strings import EventName

SINGLE_DEDUP_STATEMENT: Final[SQL] = SQL("""INSERT INTO {event_dedup_table}
    ({event_id_col}, {ack_time_col}, {event_name_col})
    VALUES ({event_id}, {acknowledgement_time}, {event_name})""")


def prepare_single_dedup_sql(
    event_id: int, event_name: EventName, acknowledgement_time: datetime | None = None
) -> Composed:
    return SINGLE_DEDUP_STATEMENT.format(
        event_dedup_table=Identifier(EventLiteral.EVENTS_TABLE_NAME),
        event_id_col=Identifier(EventLiteral.EVENT_ID_COLUMN_NAME),
        ack_time_col=Identifier(EventLiteral.EVENT_TIMESTAMP_COLUMN_NAME),
        event_name_col=Identifier(EventLiteral.EVENT_NAME_COLUMN_NAME),
        event_id=SQL_Literal(event_id),
        acknowledgement_time=SQL_Literal(acknowledgement_time or datetime.now()),
        event_name=SQL_Literal(event_name),
    )


BATCH_DEDUP_STATEMENT: Final[SQL] = SQL("""WITH attempted AS (
        INSERT INTO {event_dedup_table} ({event_id_col}, {ack_time_col}, {event_name_col})
        SELECT {event_id_col}, {ack_time_col}, {event_name_col}
        FROM {temp_table}
        ON CONFLICT ({event_id_col}) DO NOTHING
        RETURNING {event_id_col}
    )
    SELECT {event_id_col} FROM attempted;""")


def prepare_batch_dedup_sql(temp_table: str) -> Composed:
    return BATCH_DEDUP_STATEMENT.format(
        event_dedup_table=Identifier(EventLiteral.EVENTS_TABLE_NAME),
        event_id_col=Identifier(EventLiteral.EVENT_ID_COLUMN_NAME),
        ack_time_col=Identifier(EventLiteral.EVENT_TIMESTAMP_COLUMN_NAME),
        event_name_col=Identifier(EventLiteral.EVENT_NAME_COLUMN_NAME),
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
        event_id_column=Identifier(EventLiteral.EVENT_ID_COLUMN_NAME),
    )


COPIED_INSERTION_SQL: Final[SQL] = SQL("""
    INSERT INTO {table}
    SELECT *
    FROM {temp_table}
    """)


def prepare_copy_insertion_sql(
    table: str,
    temp_table: str,
) -> Composed:
    return COPIED_INSERTION_SQL.format(
        table=Identifier(table),
        temp_table=Identifier(temp_table),
    )


SIDE_EFFECTS_READ_SQL: Final[SQL] = SQL("""
    SELECT * FROM {side_effects_table}
    AND {emitted_column_name} = false
    LIMIT 1
    FOR NO KEY UPDATE
    SKIP LOCKED;
    """)


def prepare_side_effects_read_sql(
    side_effects_table_name: SideEffectsTables,
) -> Composed:
    return SIDE_EFFECTS_READ_SQL.format(
        side_effects_table=Identifier(side_effects_table_name),
        emitted_column_name=Identifier(SideEffectsLiteral.SIDE_EFFECTS_EMITTED),
    )


SIDE_EFFECTS_PROCESSED_SQL: Final[SQL] = SQL("""
    UPDATE {side_effects_table}
    SET {side_effect_emitted_column} = true,
    {side_effects_payload_column} = NULL
    WHERE {root_event_column} = {root_event_id};
    """)


def prepare_side_effects_processing_sql(
    side_effects_table: SideEffectsTables, parent_event_id: int
) -> Composed:
    return SIDE_EFFECTS_PROCESSED_SQL.format(
        side_effects_table=Identifier(side_effects_table),
        side_effects_emitted_column=Identifier(SideEffectsLiteral.SIDE_EFFECTS_EMITTED),
        side_effects_payload_column=Identifier(SideEffectsLiteral.SIDE_EFFECTS_PAYLOAD),
        root_event_column=Identifier(EventLiteral.EVENT_ID_COLUMN_NAME),
        root_event_id=SQL_Literal(parent_event_id),
    )
