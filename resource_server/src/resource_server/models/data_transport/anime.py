from typing import ClassVar

from sqlalchemy.util.typing import Self

from resource_server.models.database import Anime, Genre, StreamLink
from resource_server.repositories.result_protocol import AbstractDTO


class AnimeResult(AbstractDTO):
    id_: int
    title: str
    members: int
    synopsis: str

    genres: list[str]
    stream_links: dict[str, str]

    COUNTER_FIELDS: ClassVar[tuple[str]] = ("members",)
    resource_name: ClassVar[str] = Anime.__tablename__

    @classmethod
    def construct_from_orm(
        cls,
        obj: Anime,
        /,
        genres: list[Genre],
        stream_links: list[StreamLink],
    ) -> Self:
        return cls(
            id_=obj.id_,
            title=obj.title,
            members=obj.members,
            synopsis=obj.synopsis,
            genres=[g.name_ for g in genres],
            stream_links={s.website: s.url for s in stream_links},
        )
