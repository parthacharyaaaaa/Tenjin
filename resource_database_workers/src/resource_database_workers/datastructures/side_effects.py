from datetime import datetime

from pydantic import BaseModel

from resource_auxillary.datastructures.database import (
    ForeignKeyColumnLiteral,
    StrongEntity,
)


class DownstreamDeletionPayload(BaseModel):
    foreign_key_column: ForeignKeyColumnLiteral
    orphan_table: StrongEntity
    foreign_key: int
    deleted_at: datetime
