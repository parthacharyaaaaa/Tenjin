from datetime import datetime
from typing import TypedDict


class CommentCreation(TypedDict):
    author_id: int
    parent_post: int
    parent_forum: int
    body: str
    time_created: datetime
