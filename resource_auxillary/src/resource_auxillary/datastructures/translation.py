from types import MappingProxyType
from typing import Final

from resource_auxillary.events import EventName
from resource_auxillary.datastructures.payloads import assosciation
from resource_auxillary.datastructures.payloads import standalone

type t_event_payload_mapping = MappingProxyType[EventName, type]
type t_event_db_metadata_mapping = MappingProxyType[EventName, str]

EVENT_PAYLOAD_TYPES: Final[t_event_payload_mapping] = MappingProxyType(
    {
        EventName.POST_CREATE: standalone.PostCreation,
        EventName.POST_SAVE: assosciation.GenericPostAssosciaation,
        EventName.POST_UNSAVE: assosciation.GenericPostAssosciaation,
        EventName.POST_VOTE: assosciation.PostVoteAssosciation,
        EventName.POST_UNVOTE: assosciation.PostVoteAssosciation,
        EventName.POST_REPORT: assosciation.PostReportAssosciation,
        EventName.COMMENT_VOTE: assosciation.CommentVoteAssosciation,
        EventName.COMMENT_CREATE: standalone.CommentCreation,
        EventName.COMMENT_UNVOTE: assosciation.CommentVoteAssosciation,
        EventName.COMMENT_REPORT: assosciation.CommentReportAssosciation,
        EventName.COMMENT_DELETE: standalone.CommentDeletion,
        EventName.FORUM_SUB: assosciation.ForumSubscriptionAssosciation,
        EventName.FORUM_UNSUB: assosciation.ForumSubscriptionAssosciation,
        EventName.ANIME_SUB: assosciation.AnimeSubscriptionAssosciation,
        EventName.ANIME_UNSUB: assosciation.AnimeSubscriptionAssosciation,
    }
)

ASSOCIATION_DB_METADATA: Final[t_event_db_metadata_mapping] = MappingProxyType(
    {
        EventName.POST_CREATE: "posts",
        EventName.POST_SAVE: "post_saves",
        EventName.POST_UNSAVE: "post_saves",
        EventName.POST_VOTE: "post_votes",
        EventName.POST_UNVOTE: "post_votes",
        EventName.POST_REPORT: "post_reports",
        EventName.COMMENT_CREATE: "comments",
        EventName.COMMENT_VOTE: "comment_votes",
        EventName.COMMENT_UNVOTE: "comment_votes",
        EventName.COMMENT_REPORT: "comment_reportss",
        EventName.COMMENT_DELETE: "comments",
        EventName.FORUM_SUB: "forum_subscriptions",
        EventName.FORUM_UNSUB: "forum_subscriptions",
        EventName.ANIME_SUB: "anime_subscriptions",
        EventName.ANIME_UNSUB: "anime_subscriptions",
    }
)
