import time
from functools import partial
from typing import Annotated, Final
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from redis.typing import FieldT

from auxillary.utils import cache_repr, json_repr, to_base64url

from resource_auxillary.cache import (
    Action,
    create_intent_flag,
    derive_cache_key,
    derive_hashmap_name,
)
from resource_auxillary.events import (
    CounterUpdate,
    IntentUpdate,
    EventSideEffects,
    Event,
)
from resource_auxillary.strings import NAME_SEPERATOR, EventName, IntentFlag, StreamName

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
    get_user_repository,
)
from resource_server.models.database import Forum, ForumAdmin, Post, Anime
from resource_server.models.requests import (
    ForumCreationModel,
    ForumUpdationModel,
    AdminAddModel,
    GenericAdminModel,
)
from resource_server.repositories.forum import (
    ForumAdminResult,
    ForumRepository,
    ForumResult,
)
from resource_server.repositories.user import UserRepository, UserResult
from resource_server.request_dependencies import (
    cursor_preprocessor,
    preprocess_sort_option,
    preprocess_timeframe,
    validate_access_token,
)
from resource_server.repositories.anime import AnimeRepository, AnimeResult
from resource_server.repositories.posts import PostRepository, PostResult
from resource_server.event_streamer import EventStreamer
from resource_server.models.database_enums import AdminRoles
from resource_server.models.admin_permissions import AdminPermissions, check_permission
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
async def add_admin(
    forum_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    admin_model: AdminAddModel,
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    forum_repo: Annotated[ForumRepository, Depends(get_forum_repository)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
) -> JSONResponse:
    forum: ForumResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(Forum.__tablename__, forum_id),
        partial(forum_repo.get_forum, forum_id),
        ForumResult,
    )
    if not forum:
        raise HTTPException(404, f"No forum with id {forum_id} found")

    forum_admin: ForumAdminResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(
            ForumAdmin.__tablename__,
            NAME_SEPERATOR.join((str(forum_id), str(access_token["sid"]))),
        ),
        partial(forum_repo.get_forum_admin, forum_id, access_token["sid"]),
        ForumAdminResult,
    )
    if not forum_admin:
        raise HTTPException(403, f"You are not an admin for forum: {forum.name_}")

    match admin_model.role:
        case AdminRoles.ADMIN:
            permission = AdminPermissions.ADD_ADMIN
        case AdminRoles.SUPER:
            permission = AdminPermissions.ADD_SUPER

    if not check_permission(forum_admin.role, permission):
        raise HTTPException(
            403, f"Insufficient permissions to add an admin of role {admin_model.role}"
        )

    existing_admin: ForumAdminResult | None = (
        await cache_manager.distributed_get_or_load(
            derive_cache_key(
                ForumAdmin.__tablename__,
                NAME_SEPERATOR.join((str(forum_id), str(admin_model.user_id))),
            ),
            partial(forum_repo.get_forum_admin, forum_id, admin_model.user_id),
            ForumAdminResult,
        )
    )
    if existing_admin:
        raise HTTPException(
            409, f"User is already an admin (role: {existing_admin.role.value})"
        )

    if not existing_admin:
        user: UserResult | None = await cache_manager.distributed_get_or_load(
            derive_cache_key(Forum.__tablename__, forum_id),
            partial(user_repo.get_user, admin_model.user_id),
            UserResult,
        )
        if not user:
            raise HTTPException(404, f"No user with id {admin_model.user_id} found")

    await forum_repo.add_forum_admin(forum_id, admin_model.user_id, admin_model.role)
    return JSONResponse(
        {
            "message": "Admin added",
            "role": admin_model.role,
            "time_added": datetime.now().isoformat(),
        },
        201,
    )


@FORUMS.delete("/{forum_id}/admins")
async def remove_admin(
    forum_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    admin_model: GenericAdminModel,
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

    forum_admin: ForumAdminResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(
            ForumAdmin.__tablename__,
            NAME_SEPERATOR.join((str(forum_id), str(access_token["sid"]))),
        ),
        partial(forum_repo.get_forum_admin, forum_id, access_token["sid"]),
        ForumAdminResult,
    )
    if not forum_admin:
        raise HTTPException(403, f"You are not an admin for forum: {forum.name_}")

    existing_admin: ForumAdminResult | None = (
        await cache_manager.distributed_get_or_load(
            derive_cache_key(
                ForumAdmin.__tablename__,
                NAME_SEPERATOR.join((str(forum_id), str(admin_model.user_id))),
            ),
            partial(forum_repo.get_forum_admin, forum_id, admin_model.user_id),
            ForumAdminResult,
        )
    )
    if not existing_admin:
        raise HTTPException(404, "Admin does not exist")

    if existing_admin.role == AdminRoles.OWNER:
        raise HTTPException(403, "Cannot change forum ownership")

    if existing_admin.role == AdminRoles.ADMIN:
        permission = AdminPermissions.REMOVE_ADMIN
    else:
        permission = AdminPermissions.REMOVE_SUPER

    if not check_permission(forum_admin.role, permission):
        raise HTTPException(
            403,
            f"Insufficient permissions to remove an admin with role {existing_admin.role}",
        )

    await forum_repo.remove_forum_admin(forum_id, existing_admin.user_id)
    return JSONResponse(
        {
            "message": "Admin removed",
            "role": existing_admin.role,
            "time_removed": datetime.now().isoformat(),
        }
    )


@FORUMS.patch("/{forum_id}/admins")
async def edit_admin_permissions(
    forum_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    admin_model: AdminAddModel,
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

    forum_admin: ForumAdminResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(
            ForumAdmin.__tablename__,
            NAME_SEPERATOR.join((str(forum_id), str(access_token["sid"]))),
        ),
        partial(forum_repo.get_forum_admin, forum_id, access_token["sid"]),
        ForumAdminResult,
    )
    if not forum_admin:
        raise HTTPException(403, f"You are not an admin for forum: {forum.name_}")

    existing_admin: ForumAdminResult | None = (
        await cache_manager.distributed_get_or_load(
            derive_cache_key(
                ForumAdmin.__tablename__,
                NAME_SEPERATOR.join((str(forum_id), str(admin_model.user_id))),
            ),
            partial(forum_repo.get_forum_admin, forum_id, admin_model.user_id),
            ForumAdminResult,
        )
    )
    if not existing_admin:
        raise HTTPException(404, "Admin does not exist")
    if existing_admin.role == AdminRoles.OWNER:
        raise HTTPException(403, "Owner permissions cannot be changed")

    if admin_model.role == existing_admin.role:
        raise HTTPException(409, "Previous and new roles identical")
    elif existing_admin.role == forum_admin.role:
        raise HTTPException(403, "Cannot change roles of peer admins")

    # Very brittle logic, but I can't see adding more admin roles anytime soon
    if admin_model.role == AdminRoles.ADMIN:
        permission = AdminPermissions.DEMOTE_TO_ADMIN
    else:
        permission = AdminPermissions.PROMOTE_TO_SUPER

    if not check_permission(forum_admin.role, permission):
        raise HTTPException(
            403, "Insufficient permissions to change role in this manner"
        )

    await forum_repo.update_admin_role(forum_id, admin_model.user_id, admin_model.role)
    return JSONResponse(
        {
            "message": "Updated role",
            "previous_role": existing_admin.role,
            "new_role": admin_model.role,
        }
    )


@FORUMS.get("/{forum_id}/admins")
async def get_forum_admins(
    forum_id: int,
    app_config: Annotated[AppConfig, Depends(get_app_config)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    forum_repo: Annotated[ForumRepository, Depends(get_forum_repository)],
) -> JSONResponse:
    pagination_cache_key: str = await cache_manager.derive_pagination_key(
        NAME_SEPERATOR.join((ForumAdmin.__tablename__, str(forum_id)))
    )

    admin_users, next_cursor = await cache_manager.distributed_pagination_get_or_load(
        pagination_cache_key,
        partial(forum_repo.get_forum_admin_users, forum_id),
        ForumAdminResult,
        fetch_dtype="string",
    )

    if next_cursor == CacheManager.CURSOR_UNDERIVABLE_SENTINEL:
        next_cursor = to_base64url(
            admin_users[-1].user_id, app_config.BUSINESS.PAGINATION_CURSOR_LENGTH
        )

    return JSONResponse(
        {"admins": [json_repr(i) for i in admin_users], "cursor": next_cursor}
    )


@FORUMS.post("/{forum_id}/subscriptions")
async def subscribe_forum(
    forum_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    forum_repo: Annotated[ForumRepository, Depends(get_forum_repository)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    cache_key: Final[str] = derive_cache_key(Forum.__tablename__, forum_id)
    forum: ForumResult | None = await cache_manager.distributed_get_or_load(
        cache_key,
        partial(forum_repo.get_forum, forum_id),
        ForumResult,
    )

    if not forum:
        raise HTTPException(404, f"No forum with ID {forum_id} exists")

    conflict_message: str = f"Already subscribed to forum: {forum.name_}"
    async with cache_manager.guard_action(
        access_token["sid"],
        forum_id,
        ForumResult.resource_name,
        Action.SUB,
        conflicting_intent=IntentFlag.RESOURCE_CREATION_PENDING_FLAG,
        intent_conflict_message=conflict_message,
    ) as latest_intent:
        intent_id: Final[str] = uuid4().hex
        if not latest_intent:
            subscribed: bool = await forum_repo.check_subscription(
                forum_id=forum_id, user_id=access_token["sid"]
            )
            if subscribed:
                await cache_manager.set_intent(
                    intent_id,
                    str(access_token["sid"]),
                    str(forum_id),
                    ForumResult.resource_name,
                    Action.SUB,
                    IntentFlag.RESOURCE_CREATION_PENDING_FLAG,
                )
                raise HTTPException(409, conflict_message)

        forum_owner: UserResult | None = await cache_manager.distributed_get_or_load(
            derive_cache_key(
                NAME_SEPERATOR.join((ForumResult.resource_name, "owner")), forum_id
            ),
            partial(forum_repo.get_forum_owner, forum_id),
            UserResult,
        )

        # NOTE: This should never happen
        if not forum_owner:
            raise HTTPException(500, "Failed to perform subscription")

        counter_updates: tuple[CounterUpdate, ...] = (
            CounterUpdate(
                counter_group=derive_hashmap_name(
                    ForumResult.resource_name, "subscriptions"
                ),
                cache_key=cache_key,
                field_name="subscribers",
                delta=1,
            ),
            CounterUpdate(
                counter_group=derive_hashmap_name(UserResult.resource_name, "aura"),
                cache_key=derive_cache_key(UserResult.resource_name, forum_owner.id_),
                field_name="aura",
                delta=1,
            ),
        )
        intent_updates: tuple[IntentUpdate, ...] = (
            IntentUpdate(
                intent_name=create_intent_flag(
                    ForumResult.resource_name,
                    Action.SUB,
                    str(access_token["sid"]),
                    str(forum_id),
                ),
                intent_flag=IntentFlag.RESOURCE_CREATION_PENDING_FLAG,
                intent_id=intent_id,
            ),
        )

        subscription_event: Event = Event(
            name=EventName.FORUM_SUB,
            payload={"forum_id": forum_id, "user_id": access_token["sid"]},
            side_effects=EventSideEffects(
                counter_updates=counter_updates, intent_updates=intent_updates
            ),  # type: ignore[reportCallIssue]
        )
        await event_streamer.emit_user_event(StreamName.FORUMS, subscription_event)
    return JSONResponse({"message": "Forum subscribed!"}, 202)


@FORUMS.delete("/{forum_id}/subscriptions")
async def unsubscribe_forum(
    forum_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    forum_repo: Annotated[ForumRepository, Depends(get_forum_repository)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    cache_key: Final[str] = derive_cache_key(ForumResult.resource_name, forum_id)
    forum: ForumResult | None = await cache_manager.distributed_get_or_load(
        cache_key,
        partial(forum_repo.get_forum, forum_id),
        ForumResult,
    )

    if not forum:
        raise HTTPException(404, f"No forum with ID {forum_id} exists")

    conflicting_message: str = f"Not subscribed to forum: {forum.name_}"
    async with cache_manager.guard_action(
        access_token["sid"],
        forum_id,
        ForumResult.resource_name,
        Action.SUB,
        conflicting_intent=IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
        intent_conflict_message=conflicting_message,
    ) as latest_intent:
        intent_id: Final[str] = uuid4().hex

        if not latest_intent:
            subscribed: bool = await forum_repo.check_subscription(
                forum_id=forum_id, user_id=access_token["sid"]
            )
            if not subscribed:
                await cache_manager.set_intent(
                    intent_id,
                    str(access_token["sid"]),
                    str(forum_id),
                    ForumResult.resource_name,
                    Action.UNSUB,
                    IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
                )
                raise HTTPException(409, conflicting_message)

        forum_owner: UserResult | None = await cache_manager.distributed_get_or_load(
            derive_cache_key(
                NAME_SEPERATOR.join((ForumResult.resource_name, "owner")), forum_id
            ),
            partial(forum_repo.get_forum_owner, forum_id),
            UserResult,
        )

        # NOTE: This should never happen
        if not forum_owner:
            raise HTTPException(500, "Failed to perform unsubscription")

        counter_updates: tuple[CounterUpdate, ...] = (
            CounterUpdate(
                counter_group=derive_hashmap_name(
                    ForumResult.resource_name, "subscriptions"
                ),
                cache_key=cache_key,
                field_name="subscribers",
                delta=-1,
            ),
            CounterUpdate(
                counter_group=derive_hashmap_name(UserResult.resource_name, "aura"),
                cache_key=derive_cache_key(UserResult.resource_name, forum_owner.id_),
                field_name="aura",
                delta=-1,
            ),
        )
        intent_updates: tuple[IntentUpdate, ...] = (
            IntentUpdate(
                intent_name=create_intent_flag(
                    ForumResult.resource_name,
                    Action.UNSUB,
                    str(access_token["sid"]),
                    str(forum_id),
                ),
                intent_flag=IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
                intent_id=intent_id,
            ),
        )

        unsubscription_event: Event = Event(
            name=EventName.FORUM_UNSUB,
            payload={"forum_id": forum_id, "user_id": access_token["sid"]},
            side_effects=EventSideEffects(
                counter_updates=counter_updates, intent_updates=intent_updates
            ),  # type: ignore[reportCallIssue]
        )

        await event_streamer.emit_user_event(StreamName.FORUMS, unsubscription_event)
    return JSONResponse({"message": "Forum unsubscribed!"}, 202)


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
        derive_cache_key(
            ForumAdmin.__tablename__,
            NAME_SEPERATOR.join((str(forum_id), str(access_token["sid"]))),
        ),
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
