from typing import Any, Final, Sequence, get_type_hints
from uuid import uuid4

from psycopg import AsyncConnection
from psycopg.sql import Composed

from resource_auxillary.events import Event
from resource_auxillary.datastructures.translation import (
    EVENT_PAYLOAD_TYPES,
    ASSOCIATION_DB_METADATA,
)
from resource_auxillary.datastructures.casting import (
    CAST_MAPPING,
    default_serializer,
)

from resource_database_workers.utils.sql_templates import (
    prepare_weak_insertion_copy_sql,
    prepare_weak_insertion_sql,
    prepare_temp_table_sql,
    format_strong_insertion_sql,
)


def resolve_entity_table(event: Event):
    return ASSOCIATION_DB_METADATA[event.name]


async def batch_insert_entities(conn: AsyncConnection, events: Sequence[Event]) -> None:
    payload_type = EVENT_PAYLOAD_TYPES.get(events[0].name)
    if not payload_type:
        return  # DLQ

    payload_field_types: dict[str, type] = get_type_hints(payload_type)

    table = resolve_entity_table(events[0])
    columns: tuple[str, ...] = tuple(payload_field_types)

    temp_table = f"_staging_{table}_{uuid4().hex}"

    async with conn.cursor() as cursor:
        await cursor.execute(prepare_temp_table_sql(temp_table, table))
        async with cursor.copy(
            prepare_weak_insertion_copy_sql(temp_table, *columns)
        ) as copy:
            for event in events:
                row: list[Any] = []
                for field, field_type in payload_field_types.items():
                    value = event.payload[field]
                    if value == "":
                        row.append(None)
                        continue

                    cast_function = CAST_MAPPING.get(field_type)
                    if cast_function:
                        row.append(cast_function(value))
                    else:
                        row.append(default_serializer(value, field_type))
                await copy.write_row(row)

        await cursor.execute(prepare_weak_insertion_sql(table, temp_table, columns))
    await conn.commit()


async def batch_insert_strong_entities(
    conn: AsyncConnection, events: Sequence[Event]
) -> None:
    payload_type = EVENT_PAYLOAD_TYPES.get(events[0].name)
    if not payload_type:
        return  # DLQ

    payload_field_types: dict[str, type] = get_type_hints(payload_type)

    table = resolve_entity_table(events[0])
    columns: tuple[str, ...] = tuple(payload_field_types)
    insertion_records: list[dict[str, Any]] = []

    for event in events:
        insertion_record: dict[str, Any] = {}
        for field, field_type in payload_field_types.items():
            value = event.payload[field]
            if value == "":
                insertion_record[field] = None
                continue

            cast_function = CAST_MAPPING.get(field_type)
            if cast_function:
                insertion_record[field] = cast_function(value)
            else:
                insertion_record[field] = default_serializer(value, field_type)
        insertion_records.append(insertion_record)

    insertion_sql: Final[Composed] = format_strong_insertion_sql(table, columns)
    async with conn.cursor() as cursor:
        await cursor.executemany(insertion_sql, insertion_records)
    await conn.commit()
