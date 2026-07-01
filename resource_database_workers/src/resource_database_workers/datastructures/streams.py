from types import MappingProxyType
from typing import Callable, Final

from resource_auxillary.strings import EventName, StreamName

from resource_database_workers.tasks.stream_readers import (
    upstream_dispatcher,
    downstream_dispatcher,
)

STREAM_CONSUMER_MAPPING: Final[MappingProxyType[StreamName, Callable]] = (
    MappingProxyType(
        {
            StreamName.ANIMES: upstream_dispatcher,
            StreamName.FORUMS: upstream_dispatcher,
            StreamName.POSTS: upstream_dispatcher,
            StreamName.COMMENTS: upstream_dispatcher,
            StreamName.USERS: upstream_dispatcher,
            StreamName.DOWNSTREAM_DELETIONS: downstream_dispatcher,
            StreamName.DOWNSTREAM_COUNTER_DECREMENTS: downstream_dispatcher,
        }
    )
)

STREAM_EVENT_MAPPING: Final[MappingProxyType[StreamName, tuple[EventName, ...]]] = (
    MappingProxyType(
        {
            StreamName.POSTS: (
                EventName.POST_CREATE,
                EventName.POST_SAVE,
                EventName.POST_UNSAVE,
                EventName.POST_REPORT,
                EventName.POST_VOTE,
                EventName.POST_UNVOTE,
                EventName.POST_DELETE,
            ),
            StreamName.COMMENTS: (
                EventName.COMMENT_CREATE,
                EventName.COMMENT_VOTE,
                EventName.COMMENT_UNVOTE,
                EventName.COMMENT_REPORT,
                EventName.COMMENT_DELETE,
            ),
            StreamName.FORUMS: (
                EventName.FORUM_SUB,
                EventName.FORUM_UNSUB,
                EventName.FORUM_DELETE,
            ),
            StreamName.ANIMES: (
                EventName.ANIME_SUB,
                EventName.ANIME_UNSUB,
            ),
            StreamName.USERS: (EventName.USER_CLEANUP,),
            StreamName.DOWNSTREAM_DELETIONS: (
                EventName.ORPHANED_POST_DELETE,
                EventName.ORPHANED_COMMENT_DELETE,
            ),
            StreamName.DOWNSTREAM_COUNTER_DECREMENTS: (
                EventName.DOWNSTREAM_USER_POST_DECREMENT,
                EventName.DOWNSTREAM_FORUM_POST_DECREMENT,
                EventName.DOWNSTREAM_USER_COMMENT_DECREMENT,
                EventName.DOWNSTREAM_POST_COMMENT_DECREMENT,
            ),
        }
    )
)
