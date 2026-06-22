from types import MappingProxyType
from typing import Final

from resource_auxillary.events import EventName
from resource_auxillary.datastructures.payloads import assosciation
from resource_auxillary.datastructures.payloads import standalone

type t_event_payload_mapping = MappingProxyType[EventName, type]
type t_event_db_metadata_mapping = MappingProxyType[
    EventName, tuple[str, tuple[str, ...]]
]

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

user_post_pk: Final[tuple[str, str]] = ("user_id", "post_id")
user_comment_pk: Final[tuple[str, str]] = ("user_id", "comment_id")
user_anime_pk: Final[tuple[str, str]] = ("user_id", "anime_id")
user_forum_pk: Final[tuple[str, str]] = ("user_id", "forum_id")

ASSOCIATION_DB_METADATA: Final[t_event_db_metadata_mapping] = MappingProxyType(
    {
        EventName.POST_SAVE: ("post_saves", user_post_pk),
        EventName.POST_UNSAVE: ("post_saves", user_post_pk),
        EventName.POST_VOTE: ("post_votes", user_post_pk),
        EventName.POST_UNVOTE: ("post_votes", user_post_pk),
        EventName.POST_REPORT: ("post_reports", user_post_pk),
        EventName.COMMENT_VOTE: ("comment_votes", user_comment_pk),
        EventName.COMMENT_UNVOTE: ("comment_votes", user_comment_pk),
        EventName.COMMENT_REPORT: ("comment_reportss", user_comment_pk),
        EventName.FORUM_SUB: ("forum_subscriptions", user_comment_pk),
        EventName.FORUM_UNSUB: ("forum_subscriptions", user_comment_pk),
        EventName.ANIME_SUB: ("anime_subscriptions", user_anime_pk),
        EventName.ANIME_UNSUB: ("anime_subscriptions", user_anime_pk),
    }
)
