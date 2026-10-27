from collections.abc import Mapping
from datetime import datetime
from typing import Any, ClassVar, Never

from resource_server.models.database import User
from resource_server.repositories.result_protocol import AbstractDTO


class UserResult(AbstractDTO):
    id_: int
    username: str
    email: str

    aura: int
    total_posts: int
    total_comments: int

    time_joined: datetime
    last_login: datetime

    COUNTER_FIELDS: ClassVar[tuple[str, ...]] = (
        "aura",
        "total_posts",
        "total_comments",
    )
    resource_name: ClassVar[str] = User.__tablename__


class PrivateUserResult(UserResult):
    pw_hash: bytes
    deleted: bool
    time_deleted: datetime | None

    @classmethod
    def construct_from_cache(cls, mapping: Mapping[str, Any], *args, **kwargs) -> Never:
        raise NotImplementedError("Cache mapping of private user data violates policy")

    def __cache_repr__(self) -> Never:
        raise NotImplementedError(
            "Cannot create cache representation of private user data"
        )
