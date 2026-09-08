from resource_auxillary.datastructures.database import EventLiteral
from resource_auxillary.datastructures.database import CacheSideEffectsLiteral
from datetime import datetime
from typing import Final, Iterable, Mapping, Sequence

from psycopg.sql import Literal, Identifier, SQL, Composed, Placeholder

from resource_auxillary.datastructures.database import (
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


STRONG_INSERTION_SQL: Final[SQL] = SQL("""INSERT INTO {table}
                                      VALUES ({placeholders});""")


def format_strong_insertion_sql(table: str, columns: Sequence[str]) -> Composed:
    return STRONG_INSERTION_SQL.format(
        table=Identifier(table),
        columns=SQL(", ").join(map(Identifier, columns)),
        placeholders=SQL(", ").join(Placeholder(column) for column in columns),
    )


_SIDE_EFFECTS_COLUMN_LITERALS: Final[
    tuple[EventLiteral, CacheSideEffectsLiteral, CacheSideEffectsLiteral]
] = (
    EventLiteral.EVENT_ID_COLUMN_NAME,
    CacheSideEffectsLiteral.CACHE_SIDE_EFFECTS_EMITTED,
    CacheSideEffectsLiteral.CACHE_SIDE_EFFECTS_PAYLOAD,
)

FORMATTED_CACHE_SIDE_EFFECTS_INSERTION_STATEMENT: Final[Composed] = (
    STRONG_INSERTION_SQL.format(
        table=Identifier(CacheSideEffectsLiteral.TABLE_NAME),
        columns=SQL(", ").join(map(Identifier, _SIDE_EFFECTS_COLUMN_LITERALS)),
        placeholders=SQL(", ").join(map(Placeholder, (_SIDE_EFFECTS_COLUMN_LITERALS))),
    )
)


DLQ_INSERTION_COMPOSED_STATEMENT: Final[Composed] = STRONG_INSERTION_SQL.format(
    table=Identifier(DeadLetterQueueLiteral.TABLE_NAME),
    placeholders=SQL(", ").join(Placeholder() * 2),
)


STRONG_DELETION_SQL: Final[SQL] = SQL("""UPDATE {table}
    SET {deletion_column} = data.{deletion_column},
    {deleted_at} = data.{deleted_at}
    {deletion_author_column} = {deletion_author_id}
    FROM (
        VALUES
        {values_collection}
    ) AS data({identifier}, {deletion_column}, {deleted_at})
    WHERE {table}.{identifier} = data.{identifier};""")


def prepare_strong_deletion_sql(
    table: str,
    identifier_column: str,
    deletion_data: Iterable[tuple[int, datetime, int]],
) -> Composed:
    return STRONG_DELETION_SQL.format(
        table=Identifier(table),
        identifier=Identifier(identifier_column),
        deletion_column=Identifier(DeletionColumnLiteral.DELETED_COLUMN_NAME),
        deleted_at=Identifier(DeletionColumnLiteral.DELETION_TIME_COLUMN_NAME),
        values_collection=SQL(", ").join(
            SQL("({}, true, {}, {})").format(*i) for i in deletion_data
        ),
    )


KILL_ORPHANS_SQL: Final[SQL] = SQL("""UPDATE {orphan_table}
    SET {deletion_column} = true,
    {deleted_at} = {deletion_time},
    {deletion_author_column} = {deletion_author_event_id}
    WHERE {parent_fk_column} = {parent_fk};""")


def prepare_orphan_deletion(
    orphan_table: str,
    parent_fk_column: str,
    parent_fk: int,
    deletion_time: datetime,
    deletion_author_id: int,
) -> Composed:
    return KILL_ORPHANS_SQL.format(
        orphan_table=Identifier(orphan_table),
        deletion_column=Identifier(DeletionColumnLiteral.DELETED_COLUMN_NAME),
        deleted_at=Identifier(DeletionColumnLiteral.DELETION_TIME_COLUMN_NAME),
        deletion_author_column=Identifier(DeletionColumnLiteral.DELETION_AUTHOR_EVENT),
        deletion_time=Literal(deletion_time),
        parent_fk_column=Identifier(parent_fk_column),
        parent_values=Literal(parent_fk),
        deletion_author_event_id=Literal(deletion_author_id),
    )


SELECT_DECREMENT_DELTAS_SQL: Final[SQL] = SQL(
    """SELECT {idenfitier_column}, COUNT({identifier_column}) AS delta
    FROM {table}
    WHERE {deletion_author_event_id_column} = {deletion_author_event_id}
    LIMIT {limit}
    OFFSET {offset};
    """
)


def prepare_deltas_selection(
    foreign_key_column: str,
    table: str,
    deletion_author_event_id: int,
    limit: int,
    offset: int,
) -> Composed:
    return SELECT_DECREMENT_DELTAS_SQL.format(
        identifier_column=Identifier(foreign_key_column),
        table=Identifier(table),
        deletion_author_event_id_column=Identifier(
            DeletionColumnLiteral.DELETION_AUTHOR_EVENT
        ),
        deletion_author_event_id=Literal(deletion_author_event_id),
        limit=Literal(limit),
        offset=Literal(offset),
    )
