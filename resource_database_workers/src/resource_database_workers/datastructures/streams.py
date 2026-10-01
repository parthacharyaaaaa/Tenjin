from types import MappingProxyType
from typing import Callable, Final

from resource_auxillary.strings import StreamName

from resource_database_workers.tasks.stream_readers import (
    dlq_dispatcher,
    upstream_dispatcher,
)

STREAM_CONSUMER_MAPPING: Final[MappingProxyType[StreamName, Callable]] = (
    MappingProxyType(
        {
            StreamName.ANIMES: upstream_dispatcher,
            StreamName.FORUMS: upstream_dispatcher,
            StreamName.POSTS: upstream_dispatcher,
            StreamName.COMMENTS: upstream_dispatcher,
            StreamName.USERS: upstream_dispatcher,
            StreamName.DEAD_LETTER_QUEUE: dlq_dispatcher,
        }
    )
)
