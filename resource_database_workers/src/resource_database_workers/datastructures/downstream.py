from types import MappingProxyType
from typing import Mapping, TypedDict

from resource_auxillary.cache import derive_hashmap_name
from resource_auxillary.datastructures.database import (
    StrongEntity,
    ForeignKeyColumnLiteral,
)
from resource_auxillary.strings import EventName


class AnonymousDownstreamDeletionData(TypedDict):
    foreign_key_column: ForeignKeyColumnLiteral
    orphan_table: StrongEntity


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

# Upstream strong entity mapped to event name,
# foreign key of downstream entity, and Redis hashmap name
type t_downstream_counter_event_metadata = tuple[
    EventName, ForeignKeyColumnLiteral, str
]
type t_downstream_decrement_mapping = Mapping[
    StrongEntity, tuple[t_downstream_counter_event_metadata, ...]
]

DOWNSTREAM_DELETION_ANONYMOUS_PAYLOAD_MAPPING: t_downstream_deletion_mapping = (
    MappingProxyType(
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
)


DOWNSTREAM_DECREMENT_MAPPING: t_downstream_decrement_mapping = MappingProxyType(
    {
        StrongEntity.USER: (
            (
                EventName.DOWNSTREAM_USER_POST_DECREMENT,
                ForeignKeyColumnLiteral.AUTHOR_ID,
                derive_hashmap_name(StrongEntity.USER, StrongEntity.POST),
            ),
            (
                EventName.DOWNSTREAM_USER_COMMENT_DECREMENT,
                ForeignKeyColumnLiteral.AUTHOR_ID,
                derive_hashmap_name(StrongEntity.USER, StrongEntity.COMMENT),
            ),
        ),
        StrongEntity.POST: (
            (
                EventName.DOWNSTREAM_POST_COMMENT_DECREMENT,
                ForeignKeyColumnLiteral.AUTHOR_ID,
                derive_hashmap_name(StrongEntity.USER, StrongEntity.COMMENT),
            ),
        ),
        StrongEntity.FORUM: (
            (
                EventName.DOWNSTREAM_FORUM_POST_DECREMENT,
                ForeignKeyColumnLiteral.AUTHOR_ID,
                derive_hashmap_name(StrongEntity.USER, StrongEntity.POST),
            ),
        ),
    }
)
