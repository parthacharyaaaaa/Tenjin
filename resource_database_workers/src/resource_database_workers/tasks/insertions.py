from resource_database_workers.utils.db import fan_out_side_effect
from resource_database_workers.datastructures.side_effects import (
    DownstreamCacheInvalidationPayload,
)
from resource_auxillary.templates.sql import prepare_side_effects_staging_table_sql
from resource_database_workers.datastructures.side_effects import (
    DownstreamDeletionPayload,
)
from resource_database_workers.datastructures.downstream import (
    DOWNSTREAM_DELETION_ANONYMOUS_PAYLOAD_MAPPING,
)
from resource_database_workers.datastructures.downstream import (
    AnonymousDownstreamDeletionData,
)
from resource_auxillary.datastructures.database import StrongEntity
from resource_database_workers.utils.sql_templates import (
    FORMATTED_CACHE_SIDE_EFFECTS_INSERTION_STATEMENT,
)
from auxillary.utils import json_repr
from typing import Any, Final, Literal, MutableSequence, Sequence, get_type_hints
from uuid import uuid4

from psycopg import AsyncConnection
from psycopg.errors import IntegrityError
from psycopg.sql import Composed

from resource_auxillary.events import StreamedEvent
from resource_auxillary.datastructures.translation import (
    EVENT_PAYLOAD_TYPES,
    ASSOCIATION_DB_METADATA,
)
from resource_auxillary.datastructures.casting import (
    CAST_MAPPING,
    default_serializer,
)
from resource_auxillary.datastructures.database import (
    EventLiteral,
    SideEffectsTables,
    SideEffectsLiteral,
)
from resource_auxillary.templates.sql import (
    prepare_temp_table_sql,
    prepare_weak_insertion_copy_sql,
    prepare_weak_insertion_sql,
)

from resource_database_workers.utils.sql_templates import (
    format_strong_insertion_sql,
)
from resource_database_workers.utils.typing import t_action_literal


def resolve_entity_metadata(event: StreamedEvent) -> tuple[str, tuple[str, ...]]:
    return ASSOCIATION_DB_METADATA[event.name]


async def batch_insert_with_isolation(
    conn: AsyncConnection,
    events: Sequence[StreamedEvent],
    successfully_inserted: MutableSequence[int],
    action: t_action_literal | None,
) -> None:
    try:
        if action:
            successfully_inserted.extend(
                await batch_insert_association_entities(conn, events, action)
            )
        else:
            successfully_inserted.extend(
                await batch_insert_strong_entities(conn, events)
            )
    except IntegrityError:
        await conn.rollback()
        if len(events) == 1:
            return
        bisected_length: int = len(events) // 2
        await batch_insert_with_isolation(
            conn, events[:bisected_length], successfully_inserted, action
        )
        await batch_insert_with_isolation(
            conn, events[bisected_length:], successfully_inserted, action
        )


async def batch_insert_association_entities(
    conn: AsyncConnection,
    events: Sequence[StreamedEvent],
    action: Literal["save", "vote", "subscribe"],
) -> list[int]:
    payload_type = EVENT_PAYLOAD_TYPES.get(events[0].name)
    if not payload_type:
        raise ValueError(f"Unknown payload type for event name {events[0].name}")

    payload_field_types: dict[str, type] = get_type_hints(payload_type)

    table, pk_columns = resolve_entity_metadata(events[0])
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

        await cursor.execute(
            prepare_weak_insertion_sql(table, temp_table, columns, pk_columns, action)
        )
        return [i[0] for i in await cursor.fetchall()]


async def batch_insert_strong_entities(
    conn: AsyncConnection, events: Sequence[StreamedEvent]
) -> list[int]:
    payload_type = EVENT_PAYLOAD_TYPES.get(events[0].name)
    if not payload_type:
        return []  # Mark entire batch as failed

    payload_field_types: dict[str, type] = get_type_hints(payload_type)

    table, *_ = resolve_entity_metadata(events[0])
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
        return []  # TODO: Add RETURNING/CTE


async def outbox_insertion(
    conn: AsyncConnection,
    events: Sequence[StreamedEvent],
) -> None:
    cache_insertion_records: list[dict[str, Any]] = []
    for event in events:
        cache_insertion_record: dict[str, Any] = {}
        if event.side_effects.cache:
            cache_insertion_record[EventLiteral.EVENT_ID_COLUMN_NAME] = event.event_id
            cache_insertion_record[SideEffectsLiteral.SIDE_EFFECTS_EMITTED] = False
            cache_insertion_record[SideEffectsLiteral.SIDE_EFFECTS_PAYLOAD] = json_repr(
                event.side_effects.cache
            )
            cache_insertion_records.append(cache_insertion_record)
    async with conn.cursor() as cursor:
        await cursor.executemany(
            FORMATTED_CACHE_SIDE_EFFECTS_INSERTION_STATEMENT,
            cache_insertion_records,
            returning=True,
        )


async def insert_downstream_deletion_outbox_entries(
    conn: AsyncConnection,
    events: Sequence[StreamedEvent],
    upstream_table: StrongEntity,
    identifier_column: str,
) -> None:
    child_deletion_data_tuple: tuple[AnonymousDownstreamDeletionData, ...] = (
        DOWNSTREAM_DELETION_ANONYMOUS_PAYLOAD_MAPPING[upstream_table]
    )

    staging_table_name = f"_downstream_deletion_staging_{upstream_table}_{uuid4().hex}"
    async with conn.cursor() as cursor:
        await cursor.execute(
            prepare_side_effects_staging_table_sql(
                staging_table_name, SideEffectsTables.DOWNSTREAM_DELETION
            )
        )
        async with cursor.copy(
            prepare_weak_insertion_copy_sql(
                staging_table_name,
                *(
                    EventLiteral.EVENT_ID_COLUMN_NAME,
                    SideEffectsLiteral.SIDE_EFFECTS_EMITTED,
                    SideEffectsLiteral.SIDE_EFFECTS_PAYLOAD,
                    SideEffectsLiteral.COPY_TARGET_TABLE,
                ),
            )
        ) as copy:
            for event in events:
                # NOTE: Order of outbox entries is non-trivial in copy.write_rows!
                # Common side-effects outbox values (referred event ID and event_emitted column)
                base_outbox_values: tuple[int, Literal[False]] = (event.event_id, False)
                # Side-effect specific values
                for child_deletion_data in child_deletion_data_tuple:
                    downstream_deletion_payload: DownstreamDeletionPayload = (
                        DownstreamDeletionPayload(
                            foreign_key=event.payload[identifier_column],
                            deleted_at=event.payload["deleted_at"],
                            **child_deletion_data,
                        )
                    )

                    downstream_cache_invalidation_payload: (
                        DownstreamCacheInvalidationPayload
                    ) = DownstreamCacheInvalidationPayload(
                        downstream_table=child_deletion_data["orphan_table"]
                    )

                    # Friendly reminder that 3.6+ dicts preserve order :3
                    # Downstream database deletion
                    await copy.write_row(
                        (
                            *base_outbox_values,
                            downstream_deletion_payload.model_dump(),
                            SideEffectsTables.DOWNSTREAM_DELETION,
                        )
                    )
                    # Downstream cache invalidation
                    await copy.write_row(
                        (
                            *base_outbox_values,
                            downstream_cache_invalidation_payload.model_dump(),
                            SideEffectsTables.DOWNSTREAM_CACHE_INVALIDATION,
                        )
                    )

            await fan_out_side_effect(
                conn,
                staging_table_name,
                (
                    SideEffectsTables.DOWNSTREAM_CACHE_INVALIDATION,
                    SideEffectsTables.DOWNSTREAM_DELETION,
                ),
            )
