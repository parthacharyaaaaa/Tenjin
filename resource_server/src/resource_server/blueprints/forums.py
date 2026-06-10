import time
from functools import partial
from typing import Annotated, Final
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from flask import g, jsonify
from werkzeug import Response
from werkzeug.exceptions import (
    BadRequest,
    InternalServerError,
)

from redis.typing import FieldT

from auxillary.utils import cache_repr, json_repr, to_base64url

from resource_auxillary.cache import Action, derive_cache_key
from resource_auxillary.strings import EventNames, IntentFlag

from resource_server.cache_manager import CacheManager
from resource_server.config.app_config import AppConfig
from resource_server.datastructures.requests import SortOption, TimeFrameOption
from resource_server.dependencies import (
    get_anime_repository,
    get_app_config,
    get_event_streamer,
    get_forum_repository,
    get_cache_manager,
    get_post_repository,
)
from resource_server.models.database import Forum, Post, Anime, User
from resource_server.models.requests import ForumCreationModel, ForumUpdationModel
from resource_server.repositories.forum import (
    ForumAdminResult,
    ForumRepository,
    ForumResult,
)
from resource_server.request_dependencies import (
    cursor_preprocessor,
    preprocess_sort_option,
    preprocess_timeframe,
    validate_access_token,
)
from resource_server.repositories.anime import AnimeRepository, AnimeResult
from resource_server.repositories.posts import PostRepository, PostResult
from resource_server.event_streamer import EventStreamer
from resource_server.repositories.user import UserResult
from resource_server.utils.typing import StandardAccessTokenClaims

FORUMS: Final[APIRouter] = APIRouter()


@FORUMS.get("/{forum_id}")
async def get_forum(
    forum_id: int,
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    forum_repo: Annotated[ForumRepository, Depends(get_forum_repository)],
) -> JSONResponse:
    forum: ForumResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(Forum.__tablename__, forum_id),
        partial(forum_repo.get_forum, forum_id),
        ForumResult,
    )

    if not forum:
        raise HTTPException(404, f"No forum with id {forum_id} found")

    return JSONResponse({"forum": json_repr(forum)})


@FORUMS.get("/{forum_id}/posts")
async def get_forum_posts(
    forum_id: int,
    cursor: Annotated[int, Depends(cursor_preprocessor)],
    sort_option: Annotated[SortOption, Depends(preprocess_sort_option)],
    timeframe_tuple: Annotated[
        tuple[TimeFrameOption, datetime], Depends(preprocess_timeframe)
    ],
    app_config: Annotated[AppConfig, Depends(get_app_config)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    forum_repo: Annotated[ForumRepository, Depends(get_forum_repository)],
    post_repo: Annotated[PostRepository, Depends(get_post_repository)],
) -> JSONResponse:
    forum: ForumResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(Forum.__tablename__, forum_id),
        partial(forum_repo.get_forum, forum_id),
        ForumResult,
    )

    if not forum:
        raise HTTPException(404, f"No forum with {forum_id} found")

    posts, next_cursor = await cache_manager.distributed_pagination_get_or_load(
        await cache_manager.derive_pagination_key(
            Post.__tablename__,
            cursor,
            Forum.__tablename__,
            str(forum_id),
            str(sort_option),
            str(timeframe_tuple[0]),
        ),
        partial(
            post_repo.get_forum_posts,
            forum_id,
            app_config.BUSINESS.PAGINATION_SIZE,
            cursor,
            sort_option,
            timeframe_tuple[1],
        ),
        PostResult,
    )

    if next_cursor == CacheManager.CURSOR_UNDERIVABLE_SENTINEL:
        next_cursor = to_base64url(
            posts[-1].id_, app_config.BUSINESS.PAGINATION_CURSOR_LENGTH
        )

    return JSONResponse({"posts": [json_repr(p) for p in posts], "cursor": next_cursor})


@FORUMS.post("/")
async def create_forum(
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    forum_model: ForumCreationModel,
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    anime_repo: Annotated[AnimeRepository, Depends(get_anime_repository)],
    forum_repo: Annotated[ForumRepository, Depends(get_forum_repository)],
) -> JSONResponse:
    anime: AnimeResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(Anime.__tablename__, forum_model.parent_anime_id),
        partial(anime_repo.get_anime, forum_model.parent_anime_id),
        AnimeResult,
    )

    if not anime:
        raise HTTPException(
            404, f"No anime with ID {forum_model.parent_anime_id} found"
        )

    existing_forum: ForumResult | None = await forum_repo.get_forum_by_name(
        forum_model.title
    )
    if existing_forum:
        conflict: HTTPException = HTTPException(
            409,
            f"A forum with this name, for anime with ID {forum_model.parent_anime_id} already exists",
        )
        setattr(conflict, "kwargs", {"forum": json_repr(existing_forum)})
        raise conflict

    created_forum: ForumResult = await forum_repo.create_forum(
        forum_model.title,
        forum_model.description,
        forum_model.parent_anime_id,
        access_token["sid"],
    )
    await cache_manager.batch_hset_with_ttl(
        [
            derive_cache_key(Forum.__tablename__, created_forum.id_),
            derive_cache_key(Anime.__tablename__, created_forum.anime),
        ],
        [cache_repr(created_forum), cache_repr(anime)],
        cache_manager.cache_config.TTL_STRONG,
    )

    return JSONResponse(
        {"message": "Forum created", "forum": json_repr(created_forum)}, 201
    )


@FORUMS.delete("/{forum_id}")
async def delete_forum(
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    forum_id: int,
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    forum_repo: Annotated[ForumRepository, Depends(get_forum_repository)],
) -> JSONResponse:
    request_time: float = time.time()
    forum: ForumResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(Forum.__tablename__, forum_id),
        partial(forum_repo.get_forum, forum_id),
        ForumResult,
    )

    if not forum:
        raise HTTPException(404, f"No forum with id {forum_id} found")

    forum_owner = await forum_repo.get_forum_owner(forum_id)

    # Validating request
    if forum_owner.id_ != access_token["sid"]:
        raise HTTPException(
            403, "You do not have the necessary permissions to delete this forum"
        )

    # Permission valid, and all other checks passed. Attempt to set lock for this action
    ...

    return JSONResponse(
        {
            "message": f"Deleted forum {forum.name_}",
            "request_time": request_time,
            "forum": json_repr(forum),
        },
        202,
    )


@FORUMS.post("/{forum_id}/admins")
def add_admin(forum_id: int) -> JSONResponse: ...


@FORUMS.delete("/{forum_id}/admins")
def remove_admin(forum_id: int) -> JSONResponse: ...


@FORUMS.patch("/{forum_id}/admins")
def edit_admin_permissions(forum_id: int) -> JSONResponse: ...


@FORUMS.get("/{forum_id}/admins")
def check_forum_admin(forum_id: int) -> JSONResponse: ...


@FORUMS.post("/{forum_id}/subscriptions")
async def subscribe_forum(
    forum_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    forum_repo: Annotated[ForumRepository, Depends(get_forum_repository)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    forum: ForumResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(Forum.__tablename__, forum_id),
        partial(forum_repo.get_forum, forum_id),
        ForumResult,
    )

    if not forum:
        raise HTTPException(404, f"No forum with ID {forum_id} exists")

    lock, latest_intent = await cache_manager.fetch_indicators(
        str(access_token["sid"]), str(forum_id), Forum.__tablename__, Action.SUB
    )
    if lock:
        raise HTTPException(409, "A request for this action is already underway")
    if latest_intent == IntentFlag.RESOURCE_CREATION_PENDING_FLAG.value:
        raise HTTPException(409, "Forum already subscribed")

    forum_owner: UserResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(Forum.__tablename__ + "owner", forum_id),
        partial(forum_repo.get_forum_owner, forum_id),
        UserResult,
    )

    # NOTE: This should never happen
    if not forum_owner:
        raise HTTPException(500, "Failed to perform subscription")

    event_body: dict[FieldT, int] = {"forum_id": forum_id}
    await event_streamer.emit_user_counter_event(
        EventNames.FORUM_SUB,
        Forum.__tablename__,
        forum_id,
        Action.SUB,
        IntentFlag.RESOURCE_CREATION_PENDING_FLAG,
        forum_owner.id_,
        event_body,
        1,
    )

    return JSONResponse({"message": "Forum subscribed!"}, 202)


@FORUMS.delete("/{forum_id}/subscriptions")
async def unsubscribe_forum(
    forum_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    forum_repo: Annotated[ForumRepository, Depends(get_forum_repository)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    forum: ForumResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(Forum.__tablename__, forum_id),
        partial(forum_repo.get_forum, forum_id),
        ForumResult,
    )

    if not forum:
        raise HTTPException(404, f"No forum with ID {forum_id} exists")

    lock, latest_intent = await cache_manager.fetch_indicators(
        str(access_token["sid"]), str(forum_id), Forum.__tablename__, Action.SUB
    )
    if lock:
        raise HTTPException(409, "A request for this action is already underway")
    if latest_intent == IntentFlag.RESOURCE_DELETION_PENDING_FLAG.value:
        raise HTTPException(409, "Forum already subscribed")

    forum_owner: UserResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(Forum.__tablename__ + "owner", forum_id),
        partial(forum_repo.get_forum_owner, forum_id),
        UserResult,
    )

    # NOTE: This should never happen
    if not forum_owner:
        raise HTTPException(500, "Failed to perform subscription")

    event_body: dict[FieldT, int] = {"forum_id": forum_id}
    await event_streamer.emit_user_counter_event(
        EventNames.FORUM_UNSUB,
        Forum.__tablename__,
        forum_id,
        Action.UNSUB,
        IntentFlag.RESOURCE_CREATION_PENDING_FLAG,
        forum_owner.id_,
        event_body,
        -1,
    )

    return JSONResponse({"message": "Forum subscribed!"}, 202)


@FORUMS.patch("/{forum_id}")
async def edit_forum(
    forum_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    forum_model: ForumUpdationModel,
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    forum_repo: Annotated[ForumRepository, Depends(get_forum_repository)],
) -> JSONResponse:
    forum: ForumResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(Forum.__tablename__, forum_id),
        partial(forum_repo.get_forum, forum_id),
        ForumResult,
    )

    if not forum:
        raise HTTPException(404, f"No forum with ID {forum_id} exists")

    admin_role: ForumAdminResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(Forum.__tablename__, forum_id),
        partial(forum_repo.get_forum_admin, forum_id, access_token["sid"]),
        ForumAdminResult,
    )

    if not admin_role:
        raise HTTPException(403, "You are not an admin for this forum")
    elif admin_role.role == "staff":  # TODO: Replace with StrEnum
        raise HTTPException(403, "You do not have access rights to edit this forum")

    updated_forum: ForumResult = await forum_repo.update_forum(
        forum_id, forum_model.title, forum_model.description, return_forum=True
    )

    return JSONResponse(
        {"message": "Forum edited succesfully", "forum": json_repr(updated_forum)}
    )
