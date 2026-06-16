from types import MappingProxyType
from typing import Final

from resource_auxillary.events import EventName
from resource_auxillary.datastructures.payloads import assosciation
from resource_auxillary.datastructures.payloads import standalone

type t_event_payload_mapping = MappingProxyType[EventName, type]

EVENT_PAYLOAD_TYPES: Final[t_event_payload_mapping] = MappingProxyType(
    {
        EventName.POST_SAVE: assosciation.GenericPostAssosciaation,
        EventName.POST_UNSAVE: assosciation.GenericPostAssosciaation,
        EventName.POST_VOTE: assosciation.PostVoteAssosciation,
        EventName.POST_UNVOTE: assosciation.PostVoteAssosciation,
        EventName.POST_REPORT: assosciation.PostReportAssosciation,
        EventName.COMMENT_VOTE: assosciation.CommentVoteAssosciation,
        EventName.COMMENT_CREATE: standalone.CommentCreation,
        EventName.COMMENT_UNVOTE: assosciation.CommentVoteAssosciation,
        EventName.COMMENT_REPORT: assosciation.CommentReportAssosciation,
        EventName.FORUM_SUB: assosciation.ForumSubscriptionAssosciation,
        EventName.FORUM_UNSUB: assosciation.ForumSubscriptionAssosciation,
        EventName.ANIME_SUB: assosciation.AnimeSubscriptionAssosciation,
        EventName.ANIME_UNSUB: assosciation.AnimeSubscriptionAssosciation,
    }
)
