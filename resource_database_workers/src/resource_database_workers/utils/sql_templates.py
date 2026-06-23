from datetime import datetime
from typing import Final, Iterable, Mapping, Sequence
from typing import Literal as typing_literal

from psycopg.sql import Literal, Identifier, SQL, Composed, Placeholder

from resource_auxillary.datastructures.database import (
    EventMetadataLiteral,
    DeletionColumnLiteral,
    DeadLetterQueueLiteral,
)

UPDATION_SQL: Final[SQL] = SQL("""UPDATE {table} t
                               SET t.{column} = t.{column} + v.delta
                               FROM (
                               VALUES
                               {values}
                               ) AS v({identifier}, delta)
                               WHERE t.{identifier} = v.{identifier};""")


def prepare_updation_sql(
    table: str, column: str, identifier: str, counter_data: Mapping[int, int]
) -> Composed:
    return UPDATION_SQL.format(
        table=Identifier(table),
        column=Identifier(column),
        identifier=Identifier(identifier),
        values=SQL(",").join(
            SQL("({}, {})").format(
                Literal(id_),
                Literal(delta),
            )
            for id_, delta in counter_data.items()
        ),
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
    action: typing_literal["save", "vote", "subscribe"],
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
        state_column=state_column,
        event_seq_column=EventMetadataLiteral.LAST_EVENT_IDENTIFIER_COLUMN_NAME,
        conflict_columns=SQL(", ").join(Identifier(c) for c in conflicting_columns),
    )


STRONG_INSERTION_SQL: Final[SQL] = SQL("""INSERT INTO {table}
                                      VALUES ({placeholders});""")


def format_strong_insertion_sql(table: str, columns: Sequence[str]) -> Composed:
    return STRONG_INSERTION_SQL.format(
        table=Identifier(table),
        columns=SQL(", ").join(map(Identifier, columns)),
        placeholders=SQL(", ").join(Placeholder(column) for column in columns),
    )


def format_dlq_insertion_sql() -> Composed:
    return STRONG_INSERTION_SQL.format(
        table=DeadLetterQueueLiteral.TABLE_NAME,
        placeholders=SQL(", ").join(Placeholder() * 2),
    )


def format_counters_dlq_insertion_sql() -> Composed:
    return STRONG_INSERTION_SQL.format(
        table=DeadLetterQueueLiteral.COUNTERS_TABLE_NAME,
        placeholders=SQL(", ").join(Placeholder() * 4),
    )


STRONG_DELETION_SQL: Final[SQL] = SQL("""UPDATE {table}
    SET {deletion_column} = data.{deletion_column},
    {deleted_at} = data.{deleted_at}
    FROM (
        VALUES
        {values_collection}
    ) AS data({identifier}, {deletion_column}, {deleted_at})
    WHERE {table}.{identifier} = data.{identifier};""")


def prepare_strong_deletion_sql(
    table: str, identifier_column: str, deletion_data: Iterable[tuple[int, datetime]]
) -> Composed:
    return STRONG_DELETION_SQL.format(
        table=Identifier(table),
        identifier=Identifier(identifier_column),
        deletion_column=DeletionColumnLiteral.DELETED_COLUMN_NAME,
        deleted_at=DeletionColumnLiteral.DELETION_TIME_COLUMN_NAME,
        values=SQL(", ").join(SQL("({}, true, {})").format(*i) for i in deletion_data),
    )


KILL_ORPHANS_SQL: Final[SQL] = SQL("""UPDATE {orphan_table}
    SET {deletion_column} = true;
    {deleted_at} = {deletion_time}
    WHERE {parent_fk_column} = {parent_fk};""")


def prepare_orphan_deletion(
    orphan_table: str, parent_fk_column: str, parent_fk: int, deletion_time: datetime
) -> Composed:
    return KILL_ORPHANS_SQL.format(
        orphan_table=orphan_table,
        deletion_column=DeletionColumnLiteral.DELETED_COLUMN_NAME,
        deleted_at=DeletionColumnLiteral.DELETION_TIME_COLUMN_NAME,
        deletion_time=Literal(deletion_time),
        parent_fk_column=parent_fk_column,
        parent_values=Literal(parent_fk),
    )


SELECT_AUTHORS_SQL: Final[SQL] = SQL(
    """SELECT {author_idenfitier_column}, COUNT({author_identifier_column}) AS delta
    FROM {table}
    WHERE {deletion_author_event_id_column} = {deletion_author_event_id}
    LIMIT {limit}
    OFFSET {offset};
    """
)


def prepare_deltas_selection(
    author_column: str,
    table: str,
    deletion_author_event_id: int,
    limit: int,
    offset: int,
) -> Composed:
    return SELECT_AUTHORS_SQL.format(
        author_identifier_column=Identifier(author_column),
        table=Identifier(table),
        deletion_author_event_id_column=Identifier(
            DeletionColumnLiteral.DELETION_AUTHOR_EVENT
        ),
        deletion_author_event_id=Literal(deletion_author_event_id),
        limit=Literal(limit),
        offset=Literal(offset),
    )
