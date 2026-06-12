from enum import StrEnum
from typing import Final, LiteralString

NAME_SEPERATOR: Final[LiteralString] = ":"


class IntentFlag(StrEnum):
    RESOURCE_CREATION_PENDING_FLAG = NAME_SEPERATOR.join(("FLAG", "C"))
    RESOURCE_CREATION_PENDING_ALT_FLAG = NAME_SEPERATOR.join(("FLAG", "CA"))
    RESOURCE_DELETION_PENDING_FLAG = NAME_SEPERATOR.join(("FLAG", "D"))


class StreamName(StrEnum):
    USER_INTERACTIONS = "USER_INTERACTIONS"
    USER_EMAILS = "USER_EMAILS"


class Action(StrEnum):
    SAVE = "SAVE"
    REPORT = "REPORT"
    VOTE = "VOTE"
    UNVOTE = "UNVOTE"
    SUB = "SUB"
    UNSUB = "UNSUB"
    DELETE = "DELETE"


class EventName(StrEnum):
    POST_SAVE = "POST_SAVE"
    POST_UNSAVE = "POST_UNSAVE"
    POST_REPORT = "POST_REPORT"
    POST_VOTE = "POST_VOTE"  # Covers both upvote and downvote
    POST_UNVOTE = "POST_UNVOTE"

    ANIME_SUB = "ANIME_SUB"
    ANIME_UNSUB = "ANIME_UNSUB"

    COMMENT_VOTE = "COMMENT_VOTE"  # Covers both upvote and downvote
    COMMENT_UNVOTE = "COMMENT_UNVOTE"
    COMMENT_REPORT = "COMMENT_REPORT"

    FORUM_SUB = "FORUM_SUB"
    FORUM_UNSUB = "FORUM_UNSUB"

    ADMIN_ASSIGN = "ADMIN_ASSIGN"
    ADMIN_REMOVE = "ADMIN_REMOVE"
