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


def reconstruct_downstream_data_from_stream(
    payload: dict[str, str],
) -> DownstreamDeletionData:
    return DownstreamDeletionData(
        foreign_key_column=ForeignKeyColumnLiteral(payload["foreign_key_column"]),
        orphan_table=StrongEntity(payload["orphan_table"]),
        foreign_key=int(payload["foreign_key"]),
        deleted_at=datetime.fromisoformat(payload["deleted_at"]),
    )


class DownstreamCounterDecrementData(TypedDict):
    hashmap_name: str
    affected_table_name: StrongEntity
    affected_column_name: ForeignKeyColumnLiteral
    deletion_author_event_id: int


def reconstruct_downstream_counter_data_from_stream(
    payload: dict[str, str],
) -> DownstreamCounterDecrementData:
    return DownstreamCounterDecrementData(
        hashmap_name=payload["hashmap_name"],
        affected_table_name=StrongEntity(payload["affected_table_name"]),
        affected_column_name=ForeignKeyColumnLiteral(payload["affected_column_name"]),
        deletion_author_event_id=int(payload["deletion_author_event_id"]),
    )


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
