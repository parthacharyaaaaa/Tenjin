from functools import partial
from typing import Annotated, Final
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from redis.typing import FieldT

from resource_server.cache_manager import CacheManager
from resource_server.repositories.anime import AnimeRepository, AnimeResult
from resource_server.repositories.forum import ForumRepository, ForumResult
from resource_server.config.app_config import AppConfig
from resource_server.dependencies import (
    get_cache_manager,
    get_app_config,
    get_anime_repository,
    get_event_streamer,
    get_forum_repository,
)
from resource_server.models.database import (
    Anime,
    AnimeSubscription,
    Forum,
    Genre,
)
from resource_server.utils.typing import StandardAccessTokenClaims
from resource_server.request_dependencies import (
    anime_genres_preprocessor,
    cursor_preprocessor,
    search_param_preprocessor,
    validate_access_token,
)
from resource_server.event_streamer import EventStreamer

from resource_auxillary.strings import Action, EventNames, IntentFlag
from resource_auxillary.cache import derive_cache_key

from auxillary.utils import (
    json_repr,
    to_base64url,
)

ANIMES: Final[APIRouter] = APIRouter()


@ANIMES.get("/{anime_id}")
async def get_anime(
    anime_id: int,
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    anime_repo: Annotated[AnimeRepository, Depends(get_anime_repository)],
) -> JSONResponse:
    anime: AnimeResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(Anime.__tablename__, anime_id),
        partial(anime_repo.get_anime, anime_id),
        AnimeResult,
        fetch_dtype="string",
    )

    if not anime:
        raise HTTPException(404, f"No anime with id {anime_id} could be found")

    return JSONResponse(anime)


@ANIMES.post("/{anime_id}/subscriptions")
async def sub_anime(
    anime_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    anime_repo: Annotated[AnimeRepository, Depends(get_anime_repository)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    anime: AnimeResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(Anime.__tablename__, anime_id),
        partial(anime_repo.get_anime, anime_id),
        AnimeResult,
        fetch_dtype="string",
    )

    if not anime:
        raise HTTPException(404, f"No anime with id {anime_id} could be found")

    lock, latest_intent = await cache_manager.fetch_indicators(
        str(access_token["sid"]), str(anime_id), Anime.__tablename__, Action.SUB
    )
    if lock:
        raise HTTPException(409, "Duplicate operation ongoing")
    if latest_intent == IntentFlag.RESOURCE_CREATION_PENDING_FLAG:
        raise HTTPException(409, f"Already subscribed to anime {anime.title}")

    intent_id: Final[str] = uuid4().hex

    if not latest_intent:
        subscription: bool = await anime_repo.check_subscription(
            anime_id, access_token["sid"]
        )
        if subscription:
            await cache_manager.set_intent(
                intent_id,
                str(access_token["sid"]),
                str(anime_id),
                AnimeSubscription.__tablename__,
                Action.SUB,
                IntentFlag.RESOURCE_CREATION_PENDING_FLAG,
            )
            raise HTTPException(409, f"Already subscribed to anime {anime.title}")

    event_body: dict[FieldT, int] = {"anime_id": anime_id}
    await event_streamer.emit_user_counter_event(
        EventNames.ANIME_SUB,
        Anime.__tablename__,
        anime_id,
        Action.SUB,
        IntentFlag.RESOURCE_CREATION_PENDING_FLAG,
        access_token["sid"],
        event_body,
        1,
    )

    return JSONResponse({"message": "subscribed!"}, 202)


@ANIMES.delete("/{anime_id}/subscriptions")
async def unsub_anime(
    anime_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    anime_repo: Annotated[AnimeRepository, Depends(get_anime_repository)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    anime: AnimeResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(Anime.__tablename__, anime_id),
        partial(anime_repo.get_anime, anime_id),
        AnimeResult,
        fetch_dtype="string",
    )

    if not anime:
        raise HTTPException(404, f"No anime with id {anime_id} could be found")

    lock, latest_intent = await cache_manager.fetch_indicators(
        str(access_token["sid"]), str(anime_id), Anime.__tablename__, Action.SUB
    )
    if lock:
        raise HTTPException(409, "Duplicate operation ongoing")
    if latest_intent == IntentFlag.RESOURCE_DELETION_PENDING_FLAG:
        raise HTTPException(409, f"Not subscribed to anime {anime.title}")

    intent_id: Final[str] = uuid4().hex

    if not latest_intent:
        subscription: bool = await anime_repo.check_subscription(
            anime_id, access_token["sid"]
        )
        if subscription:
            await cache_manager.set_intent(
                intent_id,
                str(access_token["sid"]),
                str(anime_id),
                AnimeSubscription.__tablename__,
                Action.UNSUB,
                IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
            )
            raise HTTPException(409, f"Not subscribed to anime {anime.title}")

    event_body: dict[FieldT, int] = {"anime_id": anime_id}
    await event_streamer.emit_user_counter_event(
        EventNames.ANIME_UNSUB,
        Anime.__tablename__,
        anime_id,
        Action.UNSUB,
        IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
        access_token["sid"],
        event_body,
        -1,
    )

    return JSONResponse({"message": "unsubscribed!"}, 202)


@ANIMES.get("/")
async def get_animes(
    cursor: Annotated[int, Depends(cursor_preprocessor)],
    genres: Annotated[list[Genre], Depends(anime_genres_preprocessor)],
    search_param: Annotated[str | None, Depends(search_param_preprocessor)],
    app_config: Annotated[AppConfig, Depends(get_app_config)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    anime_repo: Annotated[AnimeRepository, Depends(get_anime_repository)],
) -> JSONResponse:
    pagination_cache_key: str = await cache_manager.derive_pagination_key(
        Anime.__tablename__,
        cursor,
        CacheManager.PAGINATION_SUB_KEY_SEPERATOR.join([str(g.id_) for g in genres]),
        search_param or "",
    )

    animes, next_cursor = await cache_manager.distributed_pagination_get_or_load(
        pagination_cache_key,
        partial(anime_repo.get_animes, cursor, search_param, genres),
        AnimeResult,
        fetch_dtype="string",
    )

    if next_cursor == CacheManager.CURSOR_UNDERIVABLE_SENTINEL:
        next_cursor = to_base64url(
            animes[-1].id_, app_config.BUSINESS.PAGINATION_CURSOR_LENGTH
        )

    return JSONResponse(
        {"animes": [json_repr(i) for i in animes], "cursor": next_cursor}
    )


@ANIMES.route("{anime_id}/links")
async def get_anime_links(
    anime_id: int,
    cache_manager: Annotated[CacheManager, get_cache_manager],
    anime_repo: Annotated[AnimeRepository, get_anime_repository],
) -> JSONResponse:
    anime: AnimeResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(Anime.__tablename__, anime_id),
        partial(anime_repo.get_anime, anime_id),
        AnimeResult,
        fetch_dtype="string",
    )

    if not anime:
        raise HTTPException(404, f"No anime with id {anime_id} could be found")

    return JSONResponse({"stream_links": anime.stream_links})


@ANIMES.get("{anime_id}/forums")
async def get_anime_forums(
    anime_id: int,
    cursor: Annotated[int, Depends(cursor_preprocessor)],
    search_param: Annotated[str | None, Depends(search_param_preprocessor)],
    app_config: Annotated[AppConfig, Depends(get_app_config)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    anime_repo: Annotated[AnimeRepository, Depends(get_anime_repository)],
    forum_repo: Annotated[ForumRepository, Depends(get_forum_repository)],
) -> JSONResponse:
    anime: AnimeResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(Anime.__tablename__, anime_id),
        partial(anime_repo.get_anime, anime_id),
        AnimeResult,
        fetch_dtype="string",
    )

    if not anime:
        raise HTTPException(404, f"No anime with id {anime_id} could be found")

    pagination_cache_key: str = await cache_manager.derive_pagination_key(
        Forum.__tablename__, cursor, search_param or ""
    )

    forums, next_cursor = await cache_manager.distributed_pagination_get_or_load(
        pagination_cache_key,
        partial(forum_repo.get_forums, cursor, search_param, anime_id),
        ForumResult,
    )

    if next_cursor == CacheManager.CURSOR_UNDERIVABLE_SENTINEL:
        next_cursor = to_base64url(
            forums[-1].id_, app_config.BUSINESS.PAGINATION_CURSOR_LENGTH
        )

    return JSONResponse(
        {"forums": [json_repr(f) for f in forums], "cursor": next_cursor}
    )
