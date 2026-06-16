from datetime import datetime
from typing import TypedDict


### Common, base assosciations ###
class BaseUserAssosciation(TypedDict):
    user_id: int


class BaseReportAssosciation(TypedDict):
    report_tag: str
    report_description: str
    report_time: datetime


class BaseSubscriptionAssosciation(TypedDict):
    time_subscribed: datetime


class BaseVoteAssosciation(TypedDict):
    vote: bool


### Posts ###
class GenericPostAssosciaation(BaseUserAssosciation):
    post_id: int


class PostReportAssosciation(GenericPostAssosciaation, BaseReportAssosciation): ...


class PostVoteAssosciation(BaseUserAssosciation, BaseVoteAssosciation): ...


### Comments ###
class GenericCommentAssosciation(BaseUserAssosciation):
    comment_id: int


class CommentReportAssosciation(GenericCommentAssosciation, BaseReportAssosciation): ...


class CommentVoteAssosciation(GenericCommentAssosciation, BaseVoteAssosciation): ...


### Forums ###
class GenericForumAssosciation(BaseUserAssosciation):
    forum_id: int


class ForumSubscriptionAssosciation(
    GenericForumAssosciation, BaseSubscriptionAssosciation
): ...


### Animes ###
class GenericAnimeAssosciation(BaseUserAssosciation):
    anime_id: int


class AnimeSubscriptionAssosciation(
    GenericAnimeAssosciation, BaseSubscriptionAssosciation
): ...
