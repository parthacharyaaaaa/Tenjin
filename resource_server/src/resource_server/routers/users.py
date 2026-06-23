import time
from datetime import datetime
from functools import partial
from typing import Annotated, Final
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import JSONResponse

from auxillary.utils import (
    bcrypt_hash_password,
    bcrypt_check_password,
    json_repr,
    to_base64url,
)

from resource_auxillary.cache import (
    create_intent_flag,
    derive_cache_key,
)
from resource_auxillary.events import (
    CacheUpdate,
    Event,
    IntentUpdate,
    EventSideEffects,
    EventName,
)
from resource_auxillary.strings import EventName, IntentFlag, Action

from resource_server.config.app_config import AppConfig
from resource_server.cache_manager import CacheManager
from resource_server.datastructures.requests import SortOption
from resource_server.dependencies import (
    get_app_config,
    get_forum_repository,
    get_post_repository,
    get_anime_repository,
    get_cache_manager,
    get_event_streamer,
    get_user_repository,
)
from resource_server.models.requests import (
    GenericUserIdentificationModel,
    UserCreationModel,
    UserLoginModel,
    UserPasswordModel,
)
from resource_server.repositories.anime import AnimeRepository, AnimeResult
from resource_server.repositories.posts import PostRepository, PostResult
from resource_server.request_dependencies import (
    cursor_preprocessor,
    preprocess_sort_option,
    validate_access_token,
)
from resource_server.repositories.user import (
    PrivateUserResult,
    UserRepository,
    UserResult,
)
from resource_server.repositories.forum import ForumRepository, ForumResult
from resource_server.config.database_constants import UserConstants
from resource_server.utils.helpers import generate_url_token
from resource_server.utils.typing import StandardAccessTokenClaims
from resource_server.event_streamer import EventStreamer

USERS: Final[APIRouter] = APIRouter()


@USERS.post("/")
async def register(
    user_model: UserCreationModel,
    app_config: Annotated[AppConfig, Depends(get_app_config)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    # TODO: Add bloom filter

    existing_users: list[UserResult] = await user_repo.get_user_by_identity(
        user_model.username, user_model.email
    )

    if existing_users:
        if len(existing_users) == 2:
            raise HTTPException(
                409,
                f"Username {user_model.username} and email {user_model.email} already taken",
            )
        elif existing_users[0].username == user_model.username:
            raise HTTPException(409, f"Username {user_model.username} already taken")
        raise HTTPException(409, f"Email {user_model.email} already taken")

    # All checks passed, user creation good to go
    pw_hash = bcrypt_hash_password(user_model.password)
    user: UserResult = await user_repo.add_user(
        user_model.username, user_model.email, pw_hash
    )
    await cache_manager.hset_with_ttl(
        derive_cache_key(UserResult.resource_name, user.id_),
        json_repr(user),
        app_config.CACHE.TTL_WEAK,
    )

    # TODO: Dispatch email event

    return JSONResponse({"message": "account created", "user": json_repr(user)}, 201)


@USERS.delete("/{username}")
async def delete_user(
    username: str,
    deletion_model: UserPasswordModel,
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    user_cache_key: Final[str] = derive_cache_key(UserResult.resource_name, username)
    user: UserResult | None = await cache_manager.distributed_get_or_load(
        user_cache_key, partial(user_repo.get_user_by_username, username), UserResult
    )
    if not user:
        raise HTTPException(404, f"User {username} not found")

    password_hash: bytes = await user_repo.get_user_password(user.id_)

    if not bcrypt_check_password(deletion_model.password, password_hash):
        raise HTTPException(403, "Incorrect password")

    # User deleting self, so both user and resource identifier are identical
    lock, latest_intent = await cache_manager.fetch_indicators(
        username, username, UserResult.resource_name, Action.DELETE
    )

    if lock:
        raise HTTPException(409, "Identical operation underway")
    if latest_intent == IntentFlag.RESOURCE_DELETION_PENDING_FLAG:
        raise HTTPException(409, "Account already queued for deletion")

    rtbf: bool = await user_repo.get_rtbf(user.id_)

    await cache_manager.set_negative_mapping(user_cache_key)
    await user_repo.delete_user(user.id_)
    deletion_event: Event = Event(
        name=EventName.USER_CLEANUP,
        event_id=uuid4().hex,
        created_at=time.time(),
        payload={"user_id": user.id_, "username": username, "rtbf": rtbf},
        side_effects=EventSideEffects(),  # type: ignore[reportCallIssue]
    )

    await event_streamer.emit_user_event(deletion_event)
    # TODO: Add mail dispatch
    return JSONResponse(
        {
            "message": "Account marked for deletion",
            "details": {
                "RTBF": rtbf,
                "info": f"Your contributions will soon be {'deleted' if rtbf else 'anonymised'}",
            },
        },
        202,
    )


@USERS.patch("/{username}/recovery")
async def recover_user(
    username: str,
    recovery_model: UserPasswordModel,
    app_config: Annotated[AppConfig, Depends(get_app_config)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    user_cache_key: Final[str] = derive_cache_key(UserResult.resource_name, username)

    user: PrivateUserResult | None = await user_repo.get_full_user_profile(username)
    if not user:
        raise HTTPException(404, f"No such user found {username}")

    if not bcrypt_check_password(recovery_model.password, user.pw_hash):
        raise HTTPException(403, "Incorrect password")

    if not user.deleted:
        # Soft deletion is instant
        raise HTTPException(409, "Account not deleted")

    last_recoverable_date = datetime.date(
        user.time_deleted + app_config.BUSINESS.ACCOUNT_RECOVERY_PERIOD
    )
    if last_recoverable_date < datetime.date(datetime.now()):
        e: HTTPException = HTTPException(410, "Account deleted and unrecoverable")
        setattr(
            e,
            "kwargs",
            {"info": f"Last recoverable date: {last_recoverable_date.isoformat()}"},
        )
        raise e

    lock, latest_intent = await cache_manager.fetch_indicators(
        username, username, UserResult.resource_name, Action.DELETE
    )
    if lock or latest_intent == IntentFlag.RESOURCE_CREATION_PENDING_FLAG:
        raise HTTPException(
            409, "A request for recovering this account is already underway"
        )

    await user_repo.recover_user(user.id_)

    intent_id: Final[str] = uuid4().hex

    intent_updates: tuple[IntentUpdate, ...] = (
        IntentUpdate(
            intent_name=create_intent_flag(
                UserResult.resource_name, Action.DELETE, username, username
            ),
            intent_flag=IntentFlag.RESOURCE_CREATION_PENDING_FLAG,
            intent_id=intent_id,
        ),
    )
    cache_updates: tuple[CacheUpdate, ...] = (
        CacheUpdate(cache_key=user_cache_key, operation="invalidate"),
    )

    recovery_event: Event = Event(
        name=EventName.RECOVERY,
        event_id=intent_id,
        created_at=time.time(),
        payload=({"user_id": user.id_, "username": username, "rtbf": user.rtbf}),
        side_effects=EventSideEffects(
            intent_updates=intent_updates, cache_invalidations=cache_updates
        ),
    )  # type: ignore[reportCallIssue]
    await event_streamer.emit_user_event(recovery_event)
    return JSONResponse(
        {
            "message": "Account recovered",
            "info": "Your contributions will become available soon",
        },
        202,
    )


@USERS.post("/recover-password")
async def recover_password(
    user_model: GenericUserIdentificationModel,
    app_config: Annotated[AppConfig, Depends(get_app_config)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    email_identity: bool = "@" in user_model.identity
    fetch_method = (
        user_repo.get_user_by_email
        if email_identity
        else user_repo.get_user_by_username
    )
    user: UserResult | None = await fetch_method(user_model.identity)
    if not user:
        raise HTTPException(
            404,
            f"No user with {'email' if email_identity else 'username'} {user_model.identity} found",
        )

    url_token: Final[str] = generate_url_token()
    await user_repo.set_password_recovery_token(
        user.id_, url_token, datetime.now() + app_config.BUSINESS.PASSWORD_TOKEN_MAX_AGE
    )

    # TODO: Enqueue email
    return JSONResponse({"message": "An email has been sent to account"}, 202)


@USERS.patch("{user_id}/update-password/{temp_url}")
async def update_password(
    user_id: int,
    temp_url: Annotated[
        str,
        Path(
            max_length=UserConstants.PASSWORD_RECOVERY_TOKEN_LENGTH,
            min_length=UserConstants.PASSWORD_RECOVERY_TOKEN_LENGTH,
        ),
    ],
    password_model: UserLoginModel,
    app_config: Annotated[AppConfig, Depends(get_app_config)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
) -> JSONResponse:
    user, (url, expiry) = await user_repo.get_user_password_recovery_token(user_id)
    if not user:
        raise HTTPException(404, f"User not found")
    elif not url:
        raise HTTPException(404, "No password recovery token found")
    elif url != temp_url:
        raise HTTPException(403, "Invalid url")
    elif expiry > datetime.now():  # type: ignore[reportOptionalOperand]
        raise HTTPException(403, "Token expired")

    pw_hash: Final[bytes] = bcrypt_hash_password(password_model.password)
    await user_repo.update_password(user_id, pw_hash)

    # User most likely to login, optimistic cache update
    await cache_manager.hset_with_ttl(
        derive_cache_key(UserResult.resource_name, user_id),
        json_repr(user),
        app_config.CACHE.TTL_WEAK,
    )
    return JSONResponse({"message": "password reset"})


@USERS.get("/{username}")
async def get_user(
    username: str,
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
) -> JSONResponse:
    user_cache_key: Final[str] = derive_cache_key(UserResult.resource_name, username)
    user: UserResult | None = await cache_manager.distributed_get_or_load(
        user_cache_key, partial(user_repo.get_user_by_username, username), UserResult
    )
    if not user:
        raise HTTPException(404, f"User {username} not found")
    return JSONResponse({"user": json_repr(user)})


@USERS.get("/{username}/posts")
async def get_user_posts(
    username: str,
    cursor: Annotated[int, Depends(cursor_preprocessor)],
    sort_option: Annotated[SortOption, Depends(preprocess_sort_option)],
    app_config: Annotated[AppConfig, Depends(get_app_config)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    post_repo: Annotated[PostRepository, Depends(get_post_repository)],
) -> JSONResponse:
    user: UserResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(UserResult.resource_name, username),
        partial(user_repo.get_user_by_username, username),
        UserResult,
    )
    if not user:
        raise HTTPException(404, f"User {username} not found")

    pagination_cache_key: Final[str] = await cache_manager.derive_pagination_key(
        PostResult.resource_name, cursor, sort_option.value
    )

    posts, next_cursor = await cache_manager.distributed_pagination_get_or_load(
        pagination_cache_key,
        partial(
            post_repo.get_user_posts,
            user.id_,
            app_config.BUSINESS.PAGINATION_SIZE,
            cursor,
            sort_option,
        ),
        PostResult,
    )

    if next_cursor == CacheManager.CURSOR_UNDERIVABLE_SENTINEL:
        next_cursor = to_base64url(
            posts[-1].id_, app_config.BUSINESS.PAGINATION_CURSOR_LENGTH
        )

    return JSONResponse(
        {"posts": [json_repr(post) for post in posts], "cursor": next_cursor}
    )


@USERS.route("/{username}/forums")
async def get_user_forums(
    username: str,
    cursor: Annotated[int, Depends(cursor_preprocessor)],
    sort_option: Annotated[SortOption, Depends(preprocess_sort_option)],
    app_config: Annotated[AppConfig, Depends(get_app_config)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    forum_repo: Annotated[ForumRepository, Depends(get_forum_repository)],
) -> JSONResponse:
    user: UserResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(UserResult.resource_name, username),
        partial(user_repo.get_user_by_username, username),
        UserResult,
    )
    if not user:
        raise HTTPException(404, f"User {username} not found")

    pagination_cache_key: Final[str] = await cache_manager.derive_pagination_key(
        ForumResult.resource_name, cursor, sort_option.value
    )

    forums, next_cursor = await cache_manager.distributed_pagination_get_or_load(
        pagination_cache_key,
        partial(
            forum_repo.get_user_forums,
            user.id_,
            app_config.BUSINESS.PAGINATION_SIZE,
            cursor,
            sort_option,
        ),
        PostResult,
    )

    if next_cursor == CacheManager.CURSOR_UNDERIVABLE_SENTINEL:
        next_cursor = to_base64url(
            forums[-1].id_, app_config.BUSINESS.PAGINATION_CURSOR_LENGTH
        )

    return JSONResponse(
        {"forums": [json_repr(forum) for forum in forums], "cursor": next_cursor}
    )


@USERS.route("/{username}/animes")
async def get_user_animes(
    username: str,
    cursor: Annotated[int, Depends(cursor_preprocessor)],
    sort_option: Annotated[SortOption, Depends(preprocess_sort_option)],
    app_config: Annotated[AppConfig, Depends(get_app_config)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    anime_repo: Annotated[AnimeRepository, Depends(get_anime_repository)],
) -> JSONResponse:
    user: UserResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(UserResult.resource_name, username),
        partial(user_repo.get_user_by_username, username),
        UserResult,
    )
    if not user:
        raise HTTPException(404, f"User {username} not found")

    pagination_cache_key: Final[str] = await cache_manager.derive_pagination_key(
        AnimeResult.resource_name, cursor, sort_option.value
    )

    animes, next_cursor = await cache_manager.distributed_pagination_get_or_load(
        pagination_cache_key,
        partial(
            anime_repo.get_user_animes,
            user.id_,
            app_config.BUSINESS.PAGINATION_SIZE,
            cursor,
            sort_option,
        ),
        PostResult,
    )

    if next_cursor == CacheManager.CURSOR_UNDERIVABLE_SENTINEL:
        next_cursor = to_base64url(
            animes[-1].id_, app_config.BUSINESS.PAGINATION_CURSOR_LENGTH
        )

    return JSONResponse(
        {"animes": [json_repr(anime) for anime in animes], "cursor": next_cursor}
    )


@USERS.post("/login")
async def login(
    user_model: UserLoginModel,
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
) -> JSONResponse:
    email_identity: bool = "@" in user_model.identity

    # Early check in case username is available
    # TODO: Add bloom filter
    user: UserResult | None = None
    if not email_identity:
        user = await cache_manager.distributed_get_or_load(
            derive_cache_key(UserResult.resource_name, user_model.identity),
            partial(user_repo.get_user_by_username, user_model.identity),
            UserResult,
        )
    else:
        user = await user_repo.get_user_by_email(user_model.identity)

    if not user:
        raise HTTPException(404, f"User {user_model.identity} not found")

    password_hash: bytes = await user_repo.get_user_password(user.id_)

    if not bcrypt_check_password(user_model.password, password_hash):
        raise HTTPException(403, "Incorrect password")

    login_time = datetime.now()
    # TODO: Add event to update user login time
    return JSONResponse(
        {
            "message": "authentication successful",
            "username": user.username,
            "id": user.id_,
            "login_time": login_time.isoformat(),
        }
    )


@USERS.patch("/{user_id}/enable-rtbf")
async def enable_rtbf(
    user_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
) -> JSONResponse:
    if user_id != access_token["sid"]:
        raise HTTPException(403, "Cannot edit another user's details")

    user_rtbf: bool = await user_repo.get_rtbf(user_id)
    if user_rtbf:
        raise HTTPException(409, "RBTF enabled")
    await user_repo.update_rtbf(user_id, True)

    return JSONResponse({"message": "RTBF enabled"})


@USERS.patch("/{user_id}/disable-rtbf")
async def disable_rtbf(
    user_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
) -> JSONResponse:
    if user_id != access_token["sid"]:
        raise HTTPException(403, "Cannot edit another user's details")

    user_rtbf: bool = await user_repo.get_rtbf(user_id)
    if not user_rtbf:
        raise HTTPException(409, "RBTF already disabled")
    await user_repo.update_rtbf(user_id, False)

    return JSONResponse({"message": "RTBF disabled"})
