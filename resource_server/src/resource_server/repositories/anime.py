from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, ClassVar, Mapping, Self, Sequence

import orjson

from redis.typing import FieldT, EncodableT

from resource_server.datastructures.requests import SortOption
from sqlalchemy import Row, UnaryExpression, and_, select, ColumnElement
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from resource_server.models.database import (
    Anime,
    AnimeSubscription,
    StreamLink,
    Genre,
    AnimeGenre,
)
from auxillary.singleton import SingletonMetaclass
from resource_server.repositories.result_protocol import AbstractResult

from resource_auxillary.strings import NAME_SEPERATOR


@dataclass(slots=True, init=False)
class AnimeResult(AbstractResult):
    id_: int
    title: str
    members: int
    synopsis: str

    genres: list[str]
    stream_links: dict[str, str]

    COUNTER_FIELDS: ClassVar[tuple[str]] = ("members",)
    resource_name: ClassVar[str] = Anime.__tablename__

    @classmethod
    def construct_from_cache(cls, mapping: Mapping[str, Any]) -> Self:
        instance = super().construct_from_cache(mapping)
        instance.genres = orjson.loads(mapping["genres"])
        instance.stream_links = orjson.loads(mapping["stream_links"])
        return instance

    @classmethod
    def construct_from_orm(
        cls,
        obj: Anime,
        genres: list[Genre],
        stream_links: list[StreamLink],
        *args,
        **kwargs,
    ) -> Self:
        instance = super().construct_from_orm(obj)
        instance.genres = [g.name_ for g in genres]
        instance.stream_links = {s.website: s.url for s in stream_links}
        return instance

    def __json_repr__(self) -> dict[str, Any]:
        return {
            "id": self.id_,
            "title": self.title,
            "members": self.members,
            "synopsis": self.synopsis,
        }

    def __cache_repr__(self) -> dict[FieldT, EncodableT]:
        return {
            "id": self.id_,
            "title": self.title,
            "members": self.members,
            "synopsis": self.synopsis,
        }


@dataclass(slots=True, weakref_slot=True)
class AnimeRepository(metaclass=SingletonMetaclass):
    session_maker: async_sessionmaker[AsyncSession]

    async def get_anime(self, anime_id: int) -> AnimeResult | None:
        async with self.session_maker() as session:
            anime: Anime | None = (
                await session.execute(select(Anime).where(Anime.id_ == anime_id))
            ).scalar_one_or_none()
            if not anime:
                return None

            genres, stream_links = await self.get_anime_details(
                anime_id, session=session
            )

            return AnimeResult.construct_from_orm(anime, genres, stream_links)

    async def get_anime_details(
        self, anime_id: int, *, session: AsyncSession | None = None
    ) -> tuple[list[Genre], list[StreamLink]]:
        local_only_session: bool = session is None
        session = session or self.session_maker()

        try:
            genres: list[Genre] = list(
                (
                    await session.execute(
                        select(Genre)
                        .join(AnimeGenre, AnimeGenre.genre_id == Genre.id_)
                        .where(AnimeGenre.anime_id == anime_id)
                    )
                )
                .scalars()
                .all()
            )

            stream_links: list[StreamLink] = list(
                (
                    await session.execute(
                        select(StreamLink).where(StreamLink.anime_id == anime_id)
                    )
                )
                .scalars()
                .all()
            )

            return genres, stream_links
        finally:
            if local_only_session:
                await session.close()

    async def check_subscription(self, anime_id: int, user_id: int) -> bool:
        async with self.session_maker() as session:
            subscription: AnimeSubscription | None = (
                await session.execute(
                    select(AnimeSubscription).where(
                        (AnimeSubscription.user_id == user_id)
                        & (AnimeSubscription.anime_id == anime_id)
                    )
                )
            ).scalar_one_or_none()

            return bool(subscription)

    @staticmethod
    def _order_anime_streams(
        stream_links: list[StreamLink],
    ) -> dict[int, list[StreamLink]]:
        anime_stream_links: defaultdict[int, list[StreamLink]] = defaultdict(list)
        for stream_link in stream_links:
            anime_stream_links[stream_link.anime_id].append(stream_link)
        return dict(anime_stream_links)

    @staticmethod
    def _order_anime_genres(
        genres: Sequence[Row[tuple[int, Genre]]],
    ) -> dict[int, list[Genre]]:
        genres_by_anime: dict[int, list[Genre]] = defaultdict(list)
        for anime_id, genre in genres:
            genres_by_anime[anime_id].append(genre)
        return dict(genres_by_anime)

    async def get_animes_details(
        self, anime_ids: Sequence[int]
    ) -> tuple[dict[int, list[Genre]], dict[int, list[StreamLink]]]:
        async with self.session_maker() as session:
            genres_result: list[Row[tuple[int, Genre]]] = list(
                (
                    await session.execute(
                        select(AnimeGenre.anime_id, Genre)
                        .select_from(AnimeGenre)
                        .join(Genre, AnimeGenre.genre_id == Genre.id_)
                        .where(AnimeGenre.anime_id.in_(anime_ids))
                    )
                ).all()
            )
            anime_genres: dict[int, list[Genre]] = self._order_anime_genres(
                genres_result
            )

            stream_links: list[StreamLink] = list(
                (
                    await session.execute(
                        select(StreamLink).where(StreamLink.anime_id.in_(anime_ids))
                    )
                )
                .scalars()
                .all()
            )

            anime_stream_links: dict[int, list[StreamLink]] = self._order_anime_streams(
                stream_links
            )

            return anime_genres, anime_stream_links

    async def get_animes(
        self,
        cursor: int = 0,
        search_param: str | None = None,
        genres: list[Genre] | None = None,
    ) -> list[AnimeResult]:
        where_clauses: list[ColumnElement] = [Anime.id_ > cursor]
        if search_param:
            where_clauses.append(Anime.title.ilike(f"%{search_param}%"))

        animes: list[Anime] = []
        stream_links: list[StreamLink] = []
        anime_genres: dict[int, list[Genre]] = {}
        async with self.session_maker() as session:
            if genres:
                animes = list(
                    (await session.execute(select(Anime).where(and_(*where_clauses))))
                    .scalars()
                    .all()
                )
                anime_ids: list[int] = [a.id_ for a in animes]
            else:
                animes = list(
                    (await session.execute(select(Anime).where(and_(*where_clauses))))
                    .scalars()
                    .all()
                )
                anime_ids: list[int] = [a.id_ for a in animes]

                genres_result: list[Row[tuple[int, Genre]]] = list(
                    (
                        await session.execute(
                            select(AnimeGenre.anime_id, Genre)
                            .select_from(AnimeGenre)
                            .join(Genre, AnimeGenre.genre_id == Genre.id_)
                            .where(AnimeGenre.anime_id.in_(anime_ids))
                        )
                    ).all()
                )
                anime_genres = self._order_anime_genres(genres_result)

            stream_links: list[StreamLink] = list(
                (
                    await session.execute(
                        select(StreamLink).where(StreamLink.anime_id.in_(anime_ids))
                    )
                )
                .scalars()
                .all()
            )

            anime_stream_links: dict[int, list[StreamLink]] = self._order_anime_streams(
                stream_links
            )

            if genres:
                return [
                    AnimeResult.construct_from_orm(
                        anime, genres, anime_stream_links[anime.id_]
                    )
                    for anime in animes
                ]
            else:
                return [
                    AnimeResult.construct_from_orm(
                        anime, anime_genres[anime.id_], anime_stream_links[anime.id_]
                    )
                    for anime in animes
                ]

    async def get_user_animes(
        self,
        user_id: int,
        limit: int,
        cursor: int = 0,
        sort_option: SortOption = SortOption.DESCENDING,
    ):
        order_clause: Callable[[], UnaryExpression] = (
            AnimeSubscription.time_subscribed.desc
        )
        if sort_option == SortOption.ASCENDING:
            order_clause = AnimeSubscription.time_subscribed.asc

        async with self.session_maker() as session:
            animes: list[Anime] = list(
                (
                    await session.execute(
                        select(Anime)
                        .select_from(AnimeSubscription)
                        .join(Anime, Anime.id_ == AnimeSubscription.anime_id)
                        .where(
                            (AnimeSubscription.user_id == user_id)
                            & (AnimeSubscription.anime_id > cursor)
                        )
                        .order_by(order_clause)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )

            anime_ids: tuple[int, ...] = tuple(a.id_ for a in animes)

            genres, stream_links = await self.get_animes_details(anime_ids)

            return [
                AnimeResult.construct_from_orm(
                    anime, genres[anime.id_], stream_links[anime.id_]
                )
                for anime in animes
            ]
