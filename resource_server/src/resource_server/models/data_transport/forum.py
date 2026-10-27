from datetime import datetime
from typing import ClassVar

from resource_server.models.data_transport.user import UserResult
from resource_server.models.database import Forum, ForumAdmin
from resource_server.models.database_enums import AdminRoles
from resource_server.repositories.result_protocol import AbstractDTO


class ForumResult(AbstractDTO):
    id_: int
    name_: str
    anime: int

    description: str | None

    subscribers: int
    posts: int
    created_at: datetime
    admin_count: int

    COUNTER_FIELDS: ClassVar[tuple[str, ...]] = ("subscribers", "posts")
    resource_name: ClassVar[str] = Forum.__tablename__


class ForumAdminResult(AbstractDTO):
    forum_id: int
    user_id: int
    role: AdminRoles

    resource_name: ClassVar[str] = ForumAdmin.__tablename__


class ForumAdminUserResult(UserResult):
    role: AdminRoles
