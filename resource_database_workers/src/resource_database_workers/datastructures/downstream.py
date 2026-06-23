from datetime import datetime
from types import MappingProxyType
from typing import Mapping, TypedDict

from resource_auxillary.datastructures.database import (
    StrongEntity,
    ForeignKeyColumnLiteral,
)


class AnonymousDownstreamDeletionData(TypedDict):
    foreign_key_column: ForeignKeyColumnLiteral
    orphan_table: StrongEntity


class DownstreamDeletionData(AnonymousDownstreamDeletionData):
    foreign_key: int
    deleted_at: datetime


FORUM_COMMENT_DOWNSTREAM_DELETION_DATA = AnonymousDownstreamDeletionData(
    foreign_key_column=ForeignKeyColumnLiteral.PARENT_FORUM,
    orphan_table=StrongEntity.COMMENT,
)

FORUM_POST_DOWNSTREAM_DELETION_DATA = AnonymousDownstreamDeletionData(
    foreign_key_column=ForeignKeyColumnLiteral.PARENT_FORUM,
    orphan_table=StrongEntity.POST,
)

USER_COMMENT_DOWNSTREAM_DELETION_DATA = AnonymousDownstreamDeletionData(
    foreign_key_column=ForeignKeyColumnLiteral.AUTHOR_ID,
    orphan_table=StrongEntity.COMMENT,
)

USER_POST_DOWNSTREAM_DELETION_DATA = AnonymousDownstreamDeletionData(
    foreign_key_column=ForeignKeyColumnLiteral.AUTHOR_ID, orphan_table=StrongEntity.POST
)

POST_COMMENT_DOWNSTREAM_DELETION_DATA = AnonymousDownstreamDeletionData(
    foreign_key_column=ForeignKeyColumnLiteral.PARENT_POST,
    orphan_table=StrongEntity.COMMENT,
)

type t_downstream_deletion_mapping = Mapping[
    StrongEntity, tuple[AnonymousDownstreamDeletionData, ...]
]

DownstreamDeletionMapping: t_downstream_deletion_mapping = MappingProxyType(
    {
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
)
