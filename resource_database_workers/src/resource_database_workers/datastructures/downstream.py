from datetime import datetime
from typing import TypedDict
from resource_auxillary.datastructures.database import StrongEntity


class AnonymousDownstreamDeletionData(TypedDict):
    foreign_key_column: str
    orphan_table: str


class DownstreamDeletionData(AnonymousDownstreamDeletionData):
    foreign_key: int
    deleted_at: datetime


FORUM_COMMENT_DOWNSTREAM_DELETION_DATA = AnonymousDownstreamDeletionData(
    foreign_key_column="parent_forum", orphan_table=StrongEntity.COMMENT.value
)

FORUM_POST_DOWNSTREAM_DELETION_DATA = AnonymousDownstreamDeletionData(
    foreign_key_column="forum_id", orphan_table=StrongEntity.POST.value
)

USER_COMMENT_DOWNSTREAM_DELETION_DATA = AnonymousDownstreamDeletionData(
    foreign_key_column="author_id", orphan_table=StrongEntity.COMMENT.value
)

USER_POST_DOWNSTREAM_DELETION_DATA = AnonymousDownstreamDeletionData(
    foreign_key_column="author_id", orphan_table=StrongEntity.POST.value
)

POST_COMMENT_DOWNSTREAM_DELETION_DATA = AnonymousDownstreamDeletionData(
    foreign_key_column="parent_post", orphan_table=StrongEntity.COMMENT.value
)

foo: dict[StrongEntity, tuple[AnonymousDownstreamDeletionData, ...]] = {
    StrongEntity.FORUM: (
        FORUM_COMMENT_DOWNSTREAM_DELETION_DATA,
        FORUM_POST_DOWNSTREAM_DELETION_DATA,
    ),
    StrongEntity.USER: (
        USER_COMMENT_DOWNSTREAM_DELETION_DATA,
        USER_POST_DOWNSTREAM_DELETION_DATA,
    ),
    StrongEntity.POST: (POST_COMMENT_DOWNSTREAM_DELETION_DATA,),
}
