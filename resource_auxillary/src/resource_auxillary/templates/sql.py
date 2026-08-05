"""SQL templates and composed strings"""

from datetime import datetime
from typing import Final

from psycopg.sql import SQL, Composed, Identifier, Literal as SQL_Literal

from resource_auxillary.datastructures.database import EventLiteral

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
