from resource_database_workers.dependencies.indicator import Inject
from resource_auxillary.datastructures.database import StrongEntity
from resource_auxillary.strings import StreamName
from resource_auxillary.datastructures.status_indicator import StatusProxy
from resource_database_workers.utils.typing import t_action_literal
import asyncio
from resource_auxillary.events import StreamedEvent
from psycopg_pool.pool_async import AsyncConnectionPool
from resource_database_workers.datastructures.queues import QueueRegistry
from resource_database_workers.dependencies import (
    get_queue_registry,
    get_internal_redis,
    get_app_redis,
    get_config,
    get_connection_pool,
)
from redis.asyncio.client import Redis
from typing import LiteralString, Final
from resource_database_workers.config.config import AppConfig
from typing import Annotated

_DEFAULT_METADATA_STRING: Final[LiteralString] = "DI Annotation"

# Global dependencies
APP_CONFIG = Annotated[AppConfig, Inject(get_config)]
APP_REDIS = Annotated[Redis, Inject(get_app_redis)]
INTERNAL_REDIS = Annotated[Redis, Inject(get_internal_redis)]
QUEUE_REGISTRY = Annotated[QueueRegistry, Inject(get_queue_registry)]
CONNECTION_POOL = Annotated[AsyncConnectionPool, Inject(get_connection_pool)]

# Worker-level dependencies
STATUS_PROXY = Annotated[StatusProxy, None]
GROUP_NAME = Annotated[str, None]
STREAM_NAME = Annotated[StreamName, None]
DEAD_LETTER_STREAM_NAME = Annotated[StreamName, None]
BATCHED_EVENT_QUEUE = Annotated[asyncio.Queue[tuple[StreamedEvent, ...]], None]
ISOLATED_EVENT_QUEUE = Annotated[asyncio.Queue[tuple[StreamedEvent]], None]

# Insertion-specific
ACTION_LITERAL = Annotated[t_action_literal | None, None]

## Deletion-specific
IDENTIFIER_COLUMN = Annotated[str, None]  # TODO: Update to StrEnum type
TABLE = Annotated[StrongEntity, None]
