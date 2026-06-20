from typing import Final, LiteralString

EVENTS_TABLE_NAME: Final[LiteralString] = "stream_events"
EVENT_ID_COLUMN_NAME: Final[LiteralString] = "event_id"
EVENT_TIMESTAMP_COLUMN_NAME: Final[LiteralString] = "acknowledgement_time"

DLQ_TABLE_NAME: Final[LiteralString] = "dlq_events"
DLQ_PAYLOAD_COLUMN_NAME: Final[LiteralString] = "payload"

COUNTERS_DLQ_TABLE_NAME: Final[LiteralString] = "counters_dlq"
COUNTERS_DLQ_FAILURE_TIME_COLUMN_NAME: Final[LiteralString] = "failure_time"
COUNTERS_DLQ_AFFECTED_RELATION_COLUMN_NAME: Final[LiteralString] = "affected_table"
COUNTERS_DLQ_AFFECTED_COLUMN_COLUMN_NAME: Final[LiteralString] = "affected_column"

LAST_EVENT_IDENTIFIER_COLUMN_NAME: Final[LiteralString] = "last_event_id"
EVENT_SAVE_COLUMN_NAME: Final[LiteralString] = "is_saved"
EVENT_SUB_COLUMN_NAME: Final[LiteralString] = "is_subscribed"
EVENT_VOTE_COLUMN_NAME: Final[LiteralString] = "vote_type"
