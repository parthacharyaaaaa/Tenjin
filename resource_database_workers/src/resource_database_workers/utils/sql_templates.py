from typing import Final, Mapping, Sequence

from psycopg.sql import Literal, Identifier, SQL, Composed, Placeholder

from resource_auxillary.events import DLQ_TABLE_NAME

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


WEAK_INSERTION_SQL: Final[SQL] = SQL("""INSERT INTO {table} ({columns})
                                     SELECT {columns}
                                     FROM {temp_table}
                                     ON CONFLICT DO NOTHING;""")


def prepare_weak_insertion_sql(
    table: str, temp_table: str, columns: Sequence[str]
) -> Composed:
    return WEAK_INSERTION_SQL.format(
        table=Identifier(table),
        columns=SQL(", ").join(map(Identifier, columns)),
        temp_table=Identifier(temp_table),
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
        table=DLQ_TABLE_NAME, placeholders=SQL(", ").join(Placeholder() * 2)
    )
