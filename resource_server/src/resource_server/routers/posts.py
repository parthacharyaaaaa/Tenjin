from datetime import datetime
from functools import partial
import time
from typing import Annotated, Final
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from auxillary.utils import cache_repr, json_repr, to_base64url

from resource_auxillary.cache import (
    NAME_SEPERATOR,
    Action,
    create_intent_flag,
    derive_cache_key,
    derive_hashmap_name,
)
from resource_auxillary.events import (
    CounterUpdate,
    Event,
    IntentUpdate,
    EventSideEffects,
    EventName,
)
from resource_auxillary.strings import EventName, IntentFlag

from resource_server.cache_manager import CacheManager
from resource_server.config.app_config import AppConfig
from resource_server.dependencies import (
    get_app_config,
    get_cache_manager,
    get_comment_repository,
    get_event_streamer,
    get_forum_repository,
    get_post_repository,
)
from resource_server.event_streamer import EventStreamer
from resource_server.models.requests import (
    PostAmendmentModel,
    PostCreationModel,
    ReportModel,
    VoteModel,
)
from resource_server.repositories.forum import (
    ForumAdminResult,
    ForumRepository,
    ForumResult,
)
from resource_server.repositories.posts import PostRepository, PostResult
from resource_server.repositories.user import UserResult
from resource_server.request_dependencies import (
    cursor_preprocessor,
    validate_access_token,
)
from resource_server.models.admin_permissions import AdminPermissions, check_permission
from resource_server.models.database import PostVote
from resource_server.repositories.comment import CommentRepository, CommentResult
from resource_server.utils.typing import StandardAccessTokenClaims
from resource_server.utils.validation import validate_duplicate_amendment_contents

POSTS: Final[APIRouter] = APIRouter()


@POSTS.post("/")
async def create_post(
    post_model: PostCreationModel,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    forum_repo: Annotated[ForumRepository, Depends(get_forum_repository)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    forum_cache_key: Final[str] = derive_cache_key(
        ForumResult.resource_name, post_model.forum_id
    )
    forum: ForumResult | None = await cache_manager.distributed_get_or_load(
        forum_cache_key, partial(forum_repo.get_forum, post_model.forum_id), ForumResult
    )
    if not forum:
        raise HTTPException(404, "Forum not found")

    intent_id: Final[str] = post_model.client_tag or uuid4().hex
    async with cache_manager.guard_action(
        access_token["sid"],
        intent_id,
        PostResult.resource_name,
        Action.CREATE,
        conflicting_intent=IntentFlag.RESOURCE_CREATION_PENDING_FLAG,
    ):
        counter_updates: tuple[CounterUpdate, ...] = (
            CounterUpdate(
                counter_group=derive_hashmap_name(ForumResult.resource_name, "posts"),
                cache_key=forum_cache_key,
                field_name="posts",
                delta=1,
            ),
            CounterUpdate(
                counter_group=derive_hashmap_name(
                    UserResult.resource_name, "total_posts"
                ),
                cache_key=derive_cache_key(
                    UserResult.resource_name, access_token["sub"]
                ),
                field_name="total_posts",
                delta=1,
            ),
        )

        event_paylaod: dict[str, str | int] = {
            "author_id": access_token["sid"],
            "forum_id": forum.id_,
            "title": post_model.title,
            "body_text": post_model.body,
            "time_posted": datetime.now().isoformat(),
        }

        post_event: Event = Event(
            name=EventName.POST_CREATE,
            event_id=intent_id,
            created_at=time.time(),
            payload=event_paylaod,
            side_effects=EventSideEffects(counter_updates=counter_updates),
        )
        await event_streamer.emit_user_event(post_event)
    return JSONResponse({"message": "post created"}, 202)


@POSTS.get("/{post_id}")
async def get_post(
    post_id: int,
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    post_repo: Annotated[PostRepository, Depends(get_post_repository)],
) -> JSONResponse:
    post: PostResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(PostResult.resource_name, post_id),
        partial(post_repo.get_post, post_id),
        PostResult,
    )

    if not post:
        raise HTTPException(404, f"No post with id {post_id} found")

    return JSONResponse(json_repr(post))


@POSTS.patch("/{post_id}")
async def edit_post(
    post_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    post_model: PostAmendmentModel,
    app_config: Annotated[AppConfig, Depends(get_app_config)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    post_repo: Annotated[PostRepository, Depends(get_post_repository)],
) -> JSONResponse:
    cache_key: Final[str] = derive_cache_key(PostResult.resource_name, post_id)
    post: PostResult | None = await cache_manager.distributed_get_or_load(
        cache_key, partial(post_repo.get_post, post_id), PostResult
    )

    if not post:
        raise HTTPException(404, f"No post with id {post_id} found")

    if post.author_id != access_token["sid"]:
        raise HTTPException(403, "Only owner can edit post details")

    if error_dict := validate_duplicate_amendment_contents(post_model, post):
        e: HTTPException = HTTPException(409, "Invalid amendment data provided")
        setattr(e, "kwargs", error_dict)
        raise e

    await post_repo.update_post(
        post_id, post_model.title, post_model.body, bool(post_model.closed)
    )
    post.body_text = post_model.body or post.body_text
    post.title = post_model.title or post.title
    if post_model.closed is not None:
        post.closed = post_model.closed

    # Enforce write-through
    await cache_manager.hset_with_ttl(
        cache_key, cache_repr(post), app_config.CACHE.TTL_STRONG
    )

    return JSONResponse({"message": "Post edited.", "post": json_repr(post)})


@POSTS.delete("/{post_id}")
async def delete_post(
    post_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    post_repo: Annotated[PostRepository, Depends(get_post_repository)],
    forum_repo: Annotated[ForumRepository, Depends(get_forum_repository)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    post: PostResult | None = await cache_manager.distributed_get_or_load(
        derive_cache_key(PostResult.resource_name, post_id),
        partial(post_repo.get_post, post_id),
        PostResult,
    )

    if not post:
        raise HTTPException(404, f"No post with id {post_id} found")

    conflicting_message: str = f"Post already deleted"
    async with cache_manager.guard_action(
        access_token["sid"],
        post_id,
        PostResult.resource_name,
        Action.DELETE,
        conflicting_intent=IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
        intent_conflict_message=conflicting_message,
    ) as latest_intent:
        intent_id: Final[str] = uuid4().hex
        if access_token["sid"] != post.author_id:
            forum_admin: ForumAdminResult | None = (
                await cache_manager.distributed_get_or_load(
                    derive_cache_key(
                        ForumAdminResult.resource_name,
                        NAME_SEPERATOR.join(
                            (str(post.forum_id), str(access_token["sid"]))
                        ),
                    ),
                    partial(
                        forum_repo.get_forum_admin, post.forum_id, access_token["sid"]
                    ),
                    ForumAdminResult,
                )
            )
            if not forum_admin:
                raise HTTPException(403, "Only author and admins can delete post")
            if not check_permission(forum_admin.role, AdminPermissions.DELETE_POST):
                raise HTTPException(403, "Insufficient permissions to delete post")

        counter_updates: tuple[CounterUpdate, ...] = (
            CounterUpdate(
                counter_group=derive_hashmap_name(ForumResult.resource_name, "posts"),
                cache_key=derive_cache_key(ForumResult.resource_name, post.forum_id),
                field_name="posts",
                delta=-1,
            ),
        )
        intent_updates: tuple[IntentUpdate, ...] = (
            IntentUpdate(
                intent_name=create_intent_flag(
                    ForumResult.resource_name,
                    Action.DELETE,
                    str(access_token["sid"]),
                    str(post_id),
                ),
                intent_flag=IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
                intent_id=intent_id,
            ),
        )

        subscription_event: Event = Event(
            name=EventName.ANIME_UNSUB,
            event_id=intent_id,
            created_at=time.time(),
            payload={"post_id": post_id},
            side_effects=EventSideEffects(
                counter_updates=counter_updates, intent_updates=intent_updates
            ),  # type: ignore[reportCallIssue]
        )

        await event_streamer.emit_user_event(subscription_event)
    return JSONResponse({"message": "post queued for deletion"}, 202)


@POSTS.post("/{post_id}/votes")
async def vote_post(
    post_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    vote_model: VoteModel,
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    post_repo: Annotated[PostRepository, Depends(get_post_repository)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    post_cache_key: Final[str] = derive_cache_key(PostResult.resource_name, post_id)
    intent: Final[IntentFlag] = (
        IntentFlag.RESOURCE_CREATION_PENDING_FLAG
        if vote_model.vote == 1
        else IntentFlag.RESOURCE_CREATION_PENDING_ALT_FLAG
    )
    delta: int = vote_model.vote

    post: PostResult | None = await cache_manager.distributed_get_or_load(
        post_cache_key, partial(post_repo.get_post, post_id), PostResult
    )
    if not post:
        raise HTTPException(404, f"No post with id {post_id} found")

    conflicting_message: str = (
        f"Post already {'upvoted' if vote_model.vote == 1 else 'downvoted'}"
    )
    async with cache_manager.guard_action(
        access_token["sid"],
        post_id,
        PostResult.resource_name,
        Action.SAVE,
        conflicting_intent=intent,
        intent_conflict_message=conflicting_message,
    ) as latest_intent:
        intent_id: Final[str] = uuid4().hex
        if not latest_intent:
            existing_vote: bool | None = await post_repo.get_vote(
                post_id, access_token["sid"]
            )
            if (existing_vote == True and vote_model.vote == 1) or (
                existing_vote == False and vote_model.vote == -1
            ):
                await cache_manager.set_intent(
                    intent_id,
                    str(access_token["sid"]),
                    str(post_id),
                    PostVote.__tablename__,
                    Action.VOTE,
                    intent,
                )
                raise HTTPException(409, "Same vote already casted")
            elif existing_vote:
                # Transitioning from upvote to downvote, or vice-versa
                delta *= 2

        counter_updates: tuple[CounterUpdate, ...] = (
            CounterUpdate(
                counter_group=derive_hashmap_name(PostResult.resource_name, "score"),
                cache_key=post_cache_key,
                field_name="score",
                delta=delta,
            ),
            CounterUpdate(
                counter_group=derive_hashmap_name(UserResult.resource_name, "aura"),
                cache_key=derive_cache_key(UserResult.resource_name, post.author_id),
                field_name="aura",
                delta=delta,
            ),
        )
        intent_updates: tuple[IntentUpdate, ...] = (
            IntentUpdate(
                intent_name=create_intent_flag(
                    PostResult.resource_name,
                    Action.VOTE,
                    str(access_token["sid"]),
                    str(post_id),
                ),
                intent_flag=intent,
                intent_id=intent_id,
            ),
        )

        vote_event: Event = Event(
            name=EventName.POST_UNSAVE,
            event_id=intent_id,
            created_at=time.time(),
            payload={"post_id": post_id, "user_id": access_token["sid"]},
            side_effects=EventSideEffects(
                counter_updates=counter_updates, intent_updates=intent_updates
            ),  # type: ignore[reportCallIssue]
        )

        await event_streamer.emit_user_event(vote_event)
    return JSONResponse({"message": "Voted"}, 202)


@POSTS.delete("/{post_id}/votes")
async def unvote_post(
    post_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    post_repo: Annotated[PostRepository, Depends(get_post_repository)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    post_cache_key: Final[str] = derive_cache_key(PostResult.resource_name, post_id)

    post: PostResult | None = await cache_manager.distributed_get_or_load(
        post_cache_key, partial(post_repo.get_post, post_id), PostResult
    )
    if not post:
        raise HTTPException(404, f"No post with id {post_id} found")

    conflicting_message: str = "No vote casted on post"
    async with cache_manager.guard_action(
        access_token["sid"],
        post_id,
        PostResult.resource_name,
        Action.SAVE,
        conflicting_intent=IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
        intent_conflict_message=conflicting_message,
    ) as latest_intent:
        intent_id: Final[str] = uuid4().hex
        delta: int = 1  # default to upvote

        if latest_intent == IntentFlag.RESOURCE_CREATION_PENDING_ALT_FLAG:  # downvote
            delta = -1
        elif not latest_intent:
            existing_vote: bool | None = await post_repo.get_vote(
                post_id, access_token["sid"]
            )
            if existing_vote is None:
                await cache_manager.set_intent(
                    intent_id,
                    str(access_token["sid"]),
                    str(post_id),
                    PostVote.__tablename__,
                    Action.VOTE,
                    IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
                )
                raise HTTPException(409, conflicting_message)
            if existing_vote == False:  # downvote
                delta = -1

        counter_updates: tuple[CounterUpdate, ...] = (
            CounterUpdate(
                counter_group=derive_hashmap_name(PostResult.resource_name, "score"),
                cache_key=post_cache_key,
                field_name="score",
                delta=delta,
            ),
            CounterUpdate(
                counter_group=derive_hashmap_name(UserResult.resource_name, "aura"),
                cache_key=derive_cache_key(UserResult.resource_name, post.author_id),
                field_name="aura",
                delta=delta,
            ),
        )
        intent_updates: tuple[IntentUpdate, ...] = (
            IntentUpdate(
                intent_name=create_intent_flag(
                    PostResult.resource_name,
                    Action.VOTE,
                    str(access_token["sid"]),
                    str(post_id),
                ),
                intent_flag=IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
                intent_id=intent_id,
            ),
        )

        unvote_event: Event = Event(
            name=EventName.POST_UNVOTE,
            event_id=intent_id,
            created_at=time.time(),
            payload={"post_id": post_id, "user_id": access_token["sid"]},
            side_effects=EventSideEffects(
                counter_updates=counter_updates, intent_updates=intent_updates
            ),  # type: ignore[reportCallIssue]
        )

        await event_streamer.emit_user_event(unvote_event)
    return JSONResponse({"message": "Unvoted"}, 202)


@POSTS.post("/{post_id}/saves")
async def save_post(
    post_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    post_repo: Annotated[PostRepository, Depends(get_post_repository)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    post_cache_key: Final[str] = derive_cache_key(PostResult.resource_name, post_id)

    post: PostResult | None = await cache_manager.distributed_get_or_load(
        post_cache_key, partial(post_repo.get_post, post_id), PostResult
    )
    if not post:
        raise HTTPException(404, f"No post with id {post_id} found")

    conflicting_message: str = "Post already saved"
    async with cache_manager.guard_action(
        access_token["sid"],
        post_id,
        PostResult.resource_name,
        Action.SAVE,
        conflicting_intent=IntentFlag.RESOURCE_CREATION_PENDING_FLAG,
        intent_conflict_message=conflicting_message,
    ) as latest_intent:
        intent_id: Final[str] = uuid4().hex
        if not latest_intent:
            if await post_repo.check_saved(post_id, access_token["sid"]):
                await cache_manager.set_intent(
                    intent_id,
                    str(access_token["sid"]),
                    str(post_id),
                    PostResult.resource_name,
                    Action.SAVE,
                    IntentFlag.RESOURCE_CREATION_PENDING_FLAG,
                )
                raise HTTPException(409, conflicting_message)

        counter_updates: tuple[CounterUpdate, ...] = (
            CounterUpdate(
                counter_group=derive_hashmap_name(PostResult.resource_name, "saves"),
                cache_key=post_cache_key,
                field_name="saves",
                delta=1,
            ),
            CounterUpdate(
                counter_group=derive_hashmap_name(UserResult.resource_name, "aura"),
                cache_key=derive_cache_key(UserResult.resource_name, post.author_id),
                field_name="aura",
                delta=1,
            ),
        )
        intent_updates: tuple[IntentUpdate, ...] = (
            IntentUpdate(
                intent_name=create_intent_flag(
                    PostResult.resource_name,
                    Action.SAVE,
                    str(access_token["sid"]),
                    str(post_id),
                ),
                intent_flag=IntentFlag.RESOURCE_CREATION_PENDING_FLAG,
                intent_id=intent_id,
            ),
        )

        save_event: Event = Event(
            name=EventName.POST_SAVE,
            event_id=intent_id,
            created_at=time.time(),
            payload={"post_id": post_id, "user_id": access_token["sid"]},
            side_effects=EventSideEffects(
                counter_updates=counter_updates, intent_updates=intent_updates
            ),  # type: ignore[reportCallIssue]
        )

        await event_streamer.emit_user_event(save_event)
    return JSONResponse({"message": "post saved"}, 202)


@POSTS.delete("/{post_id}/saves")
async def unsave_post(
    post_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    post_repo: Annotated[PostRepository, Depends(get_post_repository)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    post_cache_key: Final[str] = derive_cache_key(PostResult.resource_name, post_id)

    post: PostResult | None = await cache_manager.distributed_get_or_load(
        post_cache_key, partial(post_repo.get_post, post_id), PostResult
    )
    if not post:
        raise HTTPException(404, f"No post with id {post_id} found")

    conflicting_message: str = "Post not saved"
    async with cache_manager.guard_action(
        access_token["sid"],
        post_id,
        PostResult.resource_name,
        Action.SAVE,
        conflicting_intent=IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
        intent_conflict_message=conflicting_message,
    ) as latest_intent:
        intent_id: Final[str] = uuid4().hex
        if not latest_intent:
            if not (await post_repo.check_saved(post_id, access_token["sid"])):
                await cache_manager.set_intent(
                    intent_id,
                    str(access_token["sid"]),
                    str(post_id),
                    PostResult.resource_name,
                    Action.SAVE,
                    IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
                )
                raise HTTPException(409, "Post not saved")

        counter_updates: tuple[CounterUpdate, ...] = (
            CounterUpdate(
                counter_group=derive_hashmap_name(PostResult.resource_name, "saves"),
                cache_key=post_cache_key,
                field_name="saves",
                delta=-1,
            ),
            CounterUpdate(
                counter_group=derive_hashmap_name(UserResult.resource_name, "aura"),
                cache_key=derive_cache_key(UserResult.resource_name, post.author_id),
                field_name="aura",
                delta=-1,
            ),
        )
        intent_updates: tuple[IntentUpdate, ...] = (
            IntentUpdate(
                intent_name=create_intent_flag(
                    PostResult.resource_name,
                    Action.SAVE,
                    str(access_token["sid"]),
                    str(post_id),
                ),
                intent_flag=IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
                intent_id=intent_id,
            ),
        )

        unsave_event: Event = Event(
            name=EventName.POST_UNSAVE,
            event_id=intent_id,
            created_at=time.time(),
            payload={"post_id": post_id, "user_id": access_token["sid"]},
            side_effects=EventSideEffects(
                counter_updates=counter_updates, intent_updates=intent_updates
            ),  # type: ignore[reportCallIssue]
        )

        await event_streamer.emit_user_event(unsave_event)
    return JSONResponse({"message": "post unsaved"}, 202)


@POSTS.post("/{post_id}/reports")
async def report_post(
    post_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    report_model: ReportModel,
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    post_repo: Annotated[PostRepository, Depends(get_post_repository)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    post_cache_key: Final[str] = derive_cache_key(PostResult.resource_name, post_id)

    post: PostResult | None = await cache_manager.distributed_get_or_load(
        post_cache_key, partial(post_repo.get_post, post_id), PostResult
    )
    if not post:
        raise HTTPException(404, f"No post with id {post_id} found")

    resource_name: str = NAME_SEPERATOR.join(
        (PostResult.resource_name, report_model.tag)
    )
    lock, latest_intent = await cache_manager.fetch_indicators(
        str(access_token["sid"]), str(post_id), resource_name, Action.SAVE
    )

    if lock:
        raise HTTPException(409, "An identical request is being processed")

    intent_id: Final[str] = uuid4().hex

    if latest_intent:
        raise HTTPException(409, "Post already reported")
    else:
        if await post_repo.check_reported(
            post_id, access_token["sid"], report_model.tag
        ):
            await cache_manager.set_intent(
                intent_id,
                str(access_token["sid"]),
                str(post_id),
                resource_name,
                Action.REPORT,
                IntentFlag.RESOURCE_CREATION_PENDING_FLAG,
            )
            raise HTTPException(
                409, f"Post already reported for reason: {report_model.tag}"
            )

    counter_updates: tuple[CounterUpdate, ...] = (
        CounterUpdate(
            counter_group=derive_hashmap_name(PostResult.resource_name, "reports"),
            cache_key=post_cache_key,
            field_name="reports",
            delta=1,
        ),
    )
    intent_updates: tuple[IntentUpdate, ...] = (
        IntentUpdate(
            intent_name=create_intent_flag(
                resource_name,
                Action.REPORT,
                str(access_token["sid"]),
                str(post_id),
            ),
            intent_flag=IntentFlag.RESOURCE_CREATION_PENDING_FLAG,
            intent_id=intent_id,
        ),
    )

    report_event: Event = Event(
        name=EventName.POST_UNSAVE,
        event_id=intent_id,
        created_at=time.time(),
        payload={
            "post_id": post_id,
            "user_id": access_token["sid"],
            "report_tag": report_model.tag,
            "report_description": report_model.description,
            "report_time": datetime.now().isoformat(),
        },
        side_effects=EventSideEffects(
            counter_updates=counter_updates, intent_updates=intent_updates
        ),  # type: ignore[reportCallIssue]
    )

    await event_streamer.emit_user_event(report_event)
    return JSONResponse({"message": "post reported"}, 202)


@POSTS.get("/{post_id}/comments")
async def get_post_comments(
    post_id: int,
    cursor: Annotated[int, Depends(cursor_preprocessor)],
    app_config: Annotated[AppConfig, Depends(get_app_config)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    post_repo: Annotated[PostRepository, Depends(get_post_repository)],
    comment_repo: Annotated[CommentRepository, Depends(get_comment_repository)],
) -> JSONResponse:
    post_cache_key: Final[str] = derive_cache_key(PostResult.resource_name, post_id)
    post: PostResult | None = await cache_manager.distributed_get_or_load(
        post_cache_key, partial(post_repo.get_post, post_id), PostResult
    )
    if not post:
        raise HTTPException(404, f"No post with id {post_id} found")

    pagination_cache_key: str = await cache_manager.derive_pagination_key(
        CommentResult.resource_name,
        cursor,
    )

    comments, next_cursor = await cache_manager.distributed_pagination_get_or_load(
        pagination_cache_key,
        partial(
            comment_repo.get_post_comments,
            post_id,
            app_config.BUSINESS.PAGINATION_SIZE,
            cursor,
        ),
        CommentResult,
    )

    if next_cursor == CacheManager.CURSOR_UNDERIVABLE_SENTINEL:
        next_cursor = to_base64url(
            comments[-1].id_, app_config.BUSINESS.PAGINATION_CURSOR_LENGTH
        )

    return JSONResponse(
        {"comments": [json_repr(i) for i in comments], "cursor": next_cursor}
    )
