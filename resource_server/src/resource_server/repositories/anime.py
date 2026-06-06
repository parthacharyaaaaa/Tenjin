import asyncio
from dataclasses import dataclass
from typing import Any, Final

import orjson
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from resource_server.cache_manager import CacheManager
from resource_server.models.database import Anime, StreamLink, Genre, AnimeGenre
from resource_server.utils.singleton import SingletonMetaclass

from auxillary.utils import rediserialize, json_repr


@dataclass(slots=True, frozen=True)
class AnimeResult:
    anime: Anime
    stream_links: list[StreamLink]
    genres: list[Genre]


@dataclass(slots=True, weakref_slot=True)
class AnimeRepository(metaclass=SingletonMetaclass):
    session_maker: async_sessionmaker[AsyncSession]
    cache_manager: CacheManager

    @staticmethod
    def derive_cache_key(key: int | str) -> str:
        return f"{Anime.__tablename__}:{key}"

    @staticmethod
    def construct_anime_from_cache(mapping: dict[str, str]) -> Anime:
        casted_mapping = {
            k: Anime.deserialization_mapping()[k](v) for k, v in mapping.items()
        }
        casted_mapping["id_"] = casted_mapping.pop("id")

        return Anime(**casted_mapping)

    @staticmethod
    def construct_cache_mapping(
        anime: Anime, genres: list[Genre], stream_links: list[StreamLink]
    ) -> dict[str, Any]:
        return rediserialize(json_repr(anime)) | {
            "genres": [g.name_ for g in genres],
            "stream_links": {link.website: link.url for link in stream_links},
        }

    async def check_anime_existence(self, anime_id: int) -> bool:
        cache_key: Final[str] = self.derive_cache_key(anime_id)
        cache_result: dict[str, Any] | None = await self.cache_manager.consult_cache(
            cache_key, dtype="string"
        )

        if cache_result:
            if self.cache_manager.cache_config.NF_SENTINEL_KEY in cache_result:
                return False
            return True

        async with self.session_maker() as session:
            anime: Anime | None = (
                await session.execute(select(Anime).where(Anime.id_ == anime_id))
            ).scalar_one_or_none()

            if not anime:
                return False

            asyncio.create_task(self._update_cache_from_anime(anime))
            return True

    async def get_anime_data(
        self, anime_id: int
    ) -> tuple[Anime, list[str], dict[str, str]] | None:
        cache_key: Final[str] = self.derive_cache_key(anime_id)
        cache_result: dict[str, Any] | None = await self.cache_manager.consult_cache(
            cache_key, dtype="string"
        )

        if cache_result:
            if self.cache_manager.cache_config.NF_SENTINEL_KEY in cache_result:
                return None
            genres = cache_result.pop("genres", None)
            stream_links = cache_result.pop("stream_links", None)

            return self.construct_anime_from_cache(cache_result), genres, stream_links

        async with self.session_maker() as session:
            anime: Anime | None = (
                await session.execute(select(Anime).where(Anime.id_ == anime_id))
            ).scalar_one_or_none()

            if not anime:
                return None

            genres, stream_links = await self.get_anime_details(
                anime.id_, session=session
            )
            asyncio.create_task(self.update_cache(anime, genres, stream_links))

            return (
                anime,
                [g.name_ for g in genres],
                {s.website: s.url for s in stream_links},
            )

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

    async def update_cache(
        self,
        anime: Anime,
        genres: list[Genre],
        stream_links: list[StreamLink],
        *,
        cache_key: str | None = None,
    ) -> None:
        cache_key = cache_key or self.derive_cache_key(anime.id_)
        anime_mapping: dict[str, Any] = self.construct_cache_mapping(
            anime, genres, stream_links
        )

        await self.cache_manager.redis_client.set(
            cache_key, orjson.dumps(anime_mapping)
        )

    async def _update_cache_from_anime(self, anime: Anime) -> None:
        genres, stream_links = await self.get_anime_details(anime.id_)
        await self.update_cache(anime, genres, stream_links)
