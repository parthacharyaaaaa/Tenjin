from datetime import datetime
from functools import partial
from typing import Annotated, Final
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from resource_auxillary.datastructures.payloads.assosciation import (
    AnimeSubscriptionAssosciation,
)
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

from resource_auxillary.cache import (
    derive_cache_key,
    derive_hashmap_name,
    create_intent_flag,
)
from resource_auxillary.events import (
    Event,
    CounterUpdate,
    EventSideEffects,
    IntentUpdate,
)
from resource_auxillary.strings import Action, EventName, IntentFlag, StreamName

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
    cache_key: Final[str] = derive_cache_key(AnimeResult.resource_name, anime_id)
    anime: AnimeResult | None = await cache_manager.distributed_get_or_load(
        cache_key,
        partial(anime_repo.get_anime, anime_id),
        AnimeResult,
        fetch_dtype="string",
    )

    if not anime:
        raise HTTPException(404, f"No anime with id {anime_id} could be found")

    conflict_message: str = f"Already subscribed to anime {anime.title}"
    async with cache_manager.guard_action(
        access_token["sid"],
        anime_id,
        AnimeResult.resource_name,
        Action.SUB,
        conflicting_intent=IntentFlag.RESOURCE_CREATION_PENDING_FLAG,
        intent_conflict_message=conflict_message,
    ) as latest_intent:
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
                raise HTTPException(409, conflict_message)

        counter_updates: tuple[CounterUpdate, ...] = (
            CounterUpdate(
                counter_group=derive_hashmap_name(
                    AnimeResult.resource_name, "subscriptions"
                ),
                cache_key=cache_key,
                field_name="subscribers",
                delta=1,
            ),
        )
        intent_updates: tuple[IntentUpdate, ...] = (
            IntentUpdate(
                intent_name=create_intent_flag(
                    AnimeResult.resource_name,
                    Action.SUB,
                    str(access_token["sid"]),
                    str(anime_id),
                ),
                intent_flag=IntentFlag.RESOURCE_CREATION_PENDING_FLAG,
                intent_id=intent_id,
            ),
        )

        payload = AnimeSubscriptionAssosciation(
            anime_id=anime_id,
            user_id=access_token["sid"],
            time_subscribed=datetime.now(),
        )

        subscription_event: Event = Event(
            name=EventName.ANIME_SUB,
            payload=payload,  # type: ignore
            side_effects=EventSideEffects(
                counter_updates=counter_updates, intent_updates=intent_updates
            ),  # type: ignore[reportCallIssue]
        )

        await event_streamer.emit_user_event(StreamName.ANIMES, subscription_event)

    return JSONResponse({"message": "subscribed!"}, 202)


@ANIMES.delete("/{anime_id}/subscriptions")
async def unsub_anime(
    anime_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    anime_repo: Annotated[AnimeRepository, Depends(get_anime_repository)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    cache_key: Final[str] = derive_cache_key(Anime.__tablename__, anime_id)
    anime: AnimeResult | None = await cache_manager.distributed_get_or_load(
        cache_key,
        partial(anime_repo.get_anime, anime_id),
        AnimeResult,
        fetch_dtype="string",
    )

    if not anime:
        raise HTTPException(404, f"No anime with id {anime_id} could be found")

    conflict_message: str = f"Not subscribed to anime {anime.title}"
    async with cache_manager.guard_action(
        access_token["sid"],
        anime_id,
        AnimeResult.resource_name,
        Action.SUB,
        conflicting_intent=IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
        intent_conflict_message=conflict_message,
    ) as latest_intent:
        intent_id: Final[str] = uuid4().hex
        if not latest_intent:
            if await anime_repo.check_subscription(anime_id, access_token["sid"]):
                await cache_manager.set_intent(
                    intent_id,
                    str(access_token["sid"]),
                    str(anime_id),
                    AnimeSubscription.__tablename__,
                    Action.UNSUB,
                    IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
                )
                raise HTTPException(409, conflict_message)

        counter_updates: tuple[CounterUpdate, ...] = (
            CounterUpdate(
                counter_group=derive_hashmap_name(
                    AnimeResult.resource_name, "subscriptions"
                ),
                cache_key=cache_key,
                field_name="subscribers",
                delta=-1,
            ),
        )
        intent_updates: tuple[IntentUpdate, ...] = (
            IntentUpdate(
                intent_name=create_intent_flag(
                    AnimeResult.resource_name,
                    Action.SUB,
                    str(access_token["sid"]),
                    str(anime_id),
                ),
                intent_flag=IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
                intent_id=intent_id,
            ),
        )

        payload = AnimeSubscriptionAssosciation(
            anime_id=anime_id,
            user_id=access_token["sid"],
            time_subscribed=datetime.now(),
        )

        subscription_event: Event = Event(
            name=EventName.ANIME_UNSUB,
            payload=payload,  # type: ignore
            side_effects=EventSideEffects(
                counter_updates=counter_updates, intent_updates=intent_updates
            ),  # type: ignore[reportCallIssue]
        )

        await event_streamer.emit_user_event(StreamName.ANIMES, subscription_event)
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


@ANIMES.route("/{anime_id}/links")
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
