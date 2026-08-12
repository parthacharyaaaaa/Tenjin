import asyncio
from dataclasses import dataclass, field
from typing import Final, Mapping
from uuid import uuid4

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import TupleRow

from redis.asyncio import Redis
from resource_auxillary.datastructures.status_indicator import StatusProxy
from resource_auxillary.events import StreamedEvent
from resource_auxillary.strings import EventName, StreamName

from email_worker.config.email_config import EmailConfig
from email_worker.dependencies import (
    get_connection_pool,
    get_email_config,
    get_queue_registry,
    get_redis_client,
)
from email_worker.datastructures.queue_registry import QueueRegistry

_QUEUE_REGISTRY: Final[QueueRegistry] = get_queue_registry()


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
    status_proxy: StatusProxy


@dataclass(slots=True, kw_only=True)
class UpstreamDispatcherInput:
    config: EmailConfig = field(default_factory=get_email_config)
    redis: Redis = field(default_factory=get_redis_client)
    queue_mapping: Mapping[
        EventName,
        asyncio.Queue[tuple[StreamedEvent, ...]] | asyncio.Queue[StreamedEvent],
    ] = field(default=_QUEUE_REGISTRY.event_queue_mapping)
    dead_letter_stream_name: StreamName = field(default=StreamName.DEAD_LETTER_QUEUE)
    stream_name: StreamName = field(default=StreamName.USER_EMAILS)
    read_history: bool = field(default=True)
    consumer_name: str = field(default_factory=lambda: uuid4().hex)
    group_name: str = field(default=get_email_config().WORKER.CONSUMER_GROUP_NAME)
    status_proxy: StatusProxy
