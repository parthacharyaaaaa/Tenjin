from datetime import datetime
from typing import TypedDict


class CommentCreation(TypedDict):
    author_id: int
    parent_post: int
    parent_forum: int
    body: str
    time_created: datetime


class CommentDeletion(TypedDict):
    comment_id: int


class PostCreation(TypedDict):
    author_id: int
    forum_id: int
    title: str
    body_text: str
    time_posted: datetime


class PostDeletion(TypedDict):
    post_id: int


class UserCleanup(TypedDict):
    user_id: int
    time_deleted: datetime
