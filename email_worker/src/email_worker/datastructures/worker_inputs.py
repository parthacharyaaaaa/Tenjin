import asyncio
from dataclasses import dataclass, field
from typing import Self

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import TupleRow

from redis.asyncio import Redis
from resource_auxillary.events import StreamedEvent
from resource_auxillary.strings import EventName, StreamName

from email_worker.config.email_config import EmailConfig
from email_worker.datastructures.streams import STREAM_EVENT_MAPPING
from email_worker.dependencies import (
    get_connection_pool,
    get_email_config,
    get_queue_registry,
    get_redis_client,
)


@dataclass(slots=True, kw_only=True)
class GeneralEmailInput:
    email_config: EmailConfig = field(default_factory=get_email_config)
    redis: Redis = field(default_factory=get_redis_client)
    connection_pool: AsyncConnectionPool[AsyncConnection[TupleRow]] = field(
        default_factory=get_connection_pool
    )
    group_name: str = field(default=get_email_config().WORKER.CONSUMER_GROUP_NAME)
    dlq_stream_name: StreamName = field(default=StreamName.USER_EMAILS)
    events_queue: asyncio.Queue[tuple[StreamedEvent, ...]]
    stream_name: StreamName

    @classmethod
    def derive_worker_args(cls, event: EventName) -> Self:
        internal_queue: asyncio.Queue[tuple[StreamedEvent, ...]] = (
            get_queue_registry().event_queue_mapping[event]
        )
        stream: StreamName = STREAM_EVENT_MAPPING[event]

        return cls(stream_name=stream, events_queue=internal_queue)
