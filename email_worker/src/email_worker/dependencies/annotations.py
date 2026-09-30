import asyncio
from typing import Annotated, Final, LiteralString

from auxillary.dependencies.indicator import Inject
from psycopg_pool.pool_async import AsyncConnectionPool
from redis.asyncio.client import Redis
from resource_auxillary.datastructures.status_indicator import StatusProxy
from resource_auxillary.event_processing.event_stream_manager import EventStreamManager
from resource_auxillary.events import StreamedEvent
from resource_auxillary.strings import StreamName

from email_worker.config.config import AppConfig
from email_worker.dependencies.injections import (
    get_app_config,
    get_connection_pool,
    get_consumer_id,
    get_dead_letter_queue_name,
    get_event_stream_manager,
    get_redis_client,
)

_DEFAULT_METADATA_STRING: Final[LiteralString] = "DI Annotation"

# Global dependencies
APP_CONFIG = Annotated[AppConfig, Inject(get_app_config)]
APP_REDIS = Annotated[Redis, Inject(get_redis_client)]
CONNECTION_POOL = Annotated[AsyncConnectionPool, Inject(get_connection_pool)]

# Worker-level dependencies
STATUS_PROXY = Annotated[StatusProxy, None]
GROUP_NAME = Annotated[str, None]
STREAM_NAME = Annotated[StreamName, None]
DEAD_LETTER_STREAM_NAME = Annotated[StreamName, Inject(get_dead_letter_queue_name)]
ISOLATED_EVENT_QUEUE = Annotated[asyncio.Queue[StreamedEvent], None]
EVENT_STREAM_MANAGER = Annotated[EventStreamManager, Inject(get_event_stream_manager)]


## Reader-specific
CONSUMER_ID = Annotated[str, Inject(get_consumer_id)]
