from datetime import datetime
import time
from functools import partial
from typing import Annotated, Any, Final
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from resource_auxillary.cache import (
    create_intent_flag,
    derive_cache_key,
    derive_hashmap_name,
)
from resource_auxillary.events import (
    CacheUpdate,
    CounterUpdate,
    Event,
    IntentUpdate,
    EventSideEffects,
    EventName,
)
from resource_auxillary.strings import NAME_SEPERATOR, EventName, IntentFlag, Action

from resource_server.cache_manager import CacheManager
from resource_server.dependencies import (
    get_comment_repository,
    get_forum_repository,
    get_post_repository,
    get_cache_manager,
    get_event_streamer,
)
from resource_server.models.requests import CommentModel, ReportModel, VoteModel
from resource_server.repositories.comment import CommentRepository, CommentResult
from resource_server.repositories.posts import PostRepository, PostResult
from resource_server.request_dependencies import validate_access_token
from resource_server.models.database import CommentVote
from resource_server.repositories.user import UserResult
from resource_server.repositories.forum import ForumAdminResult, ForumRepository
from resource_server.models.admin_permissions import AdminPermissions, check_permission
from resource_server.utils.typing import StandardAccessTokenClaims
from resource_server.event_streamer import EventStreamer

COMMENTS: Final[APIRouter] = APIRouter()


@COMMENTS.post("/")
async def comment_on_post(
    post_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    comment_model: CommentModel,
    post_repo: Annotated[PostRepository, Depends(get_post_repository)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    post_cache_key: Final[str] = derive_cache_key(PostResult.resource_name, post_id)

    post: PostResult | None = await cache_manager.distributed_get_or_load(
        post_cache_key, partial(post_repo.get_post, post_id), PostResult
    )
    if not post:
        raise HTTPException(404, f"No post with id {post_id} found")
    if post.closed:
        raise HTTPException(409, "Post closed")

    intent_id: Final[str] = comment_model.client_tag or uuid4().hex

    lock, latest_intent = await cache_manager.fetch_indicators(
        str(access_token["sid"]),
        intent_id,
        CommentResult.resource_name,
        Action.CREATE,
    )

    if lock or latest_intent:
        raise HTTPException(409, "Identical request being processed")

    counter_updates: tuple[CounterUpdate, ...] = (
        CounterUpdate(
            counter_group=derive_hashmap_name(
                PostResult.resource_name, "total_comments"
            ),
            cache_key=derive_cache_key(PostResult.resource_name, post_id),
            field_name="total_comments",
            delta=1,
        ),
        CounterUpdate(
            counter_group=derive_hashmap_name(
                UserResult.resource_name, "total_comments"
            ),
            cache_key=derive_cache_key(UserResult.resource_name, access_token["sid"]),
            field_name="total_comments",
            delta=1,
        ),
    )

    comment_payload: dict[str, Any] = {
        "author_id": access_token["sid"],
        "parent_forum": post.forum_id,
        "parent_post": post.id_,
        "body": comment_model.body,
    }

    deletion_event: Event = Event(
        name=EventName.COMMENT_CREATE,
        event_id=intent_id,
        created_at=time.time(),
        payload=comment_payload,
        side_effects=EventSideEffects(
            counter_updates=counter_updates  # type: ignore[reportCallIssue]
        ),
    )

    await event_streamer.emit_user_event(deletion_event)
    return JSONResponse({"message": "Comment created"}, 202)


@COMMENTS.delete("/{comment_id}")
async def delete_comment(
    post_id: int,
    comment_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    comment_repo: Annotated[CommentRepository, Depends(get_comment_repository)],
    forum_repo: Annotated[ForumRepository, Depends(get_forum_repository)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    comment_cache_key: Final[str] = derive_cache_key(
        CommentResult.resource_name, comment_id
    )
    comment: CommentResult | None = await cache_manager.distributed_get_or_load(
        comment_cache_key, partial(comment_repo.get_comment, comment_id), CommentResult
    )

    if not comment:
        raise HTTPException(404, "Comment not found")
    if comment.author_id != access_token["sid"]:
        # Check for forum admin
        forum_admin: ForumAdminResult | None = (
            await cache_manager.distributed_get_or_load(
                derive_cache_key(
                    ForumAdminResult.resource_name,
                    NAME_SEPERATOR.join(
                        (str(comment.parent_forum), str(access_token["sid"]))
                    ),
                ),
                partial(
                    forum_repo.get_forum_admin,
                    comment.parent_forum,
                    access_token["sid"],
                ),
                ForumAdminResult,
            )
        )
        if not forum_admin:
            raise HTTPException(403, "Only author and admins can delete comments")
        if not check_permission(forum_admin.role, AdminPermissions.DELETE_COMMENT):
            raise HTTPException(403, "Insufficient permissions to delete comment")

    intent_id: Final[str] = uuid4().hex
    conflict_message: str = f"Already subscribed to forum: {forum.name_}"
    async with cache_manager.guard_action(
        access_token["sid"],
        comment_id,
        CommentResult.resource_name,
        Action.DELETE,
        conflicting_intent=IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
        intent_conflict_message=conflict_message,
    ):
        counter_updates: tuple[CounterUpdate, ...] = (
            CounterUpdate(
                counter_group=derive_hashmap_name(
                    PostResult.resource_name, "total_comments"
                ),
                cache_key=derive_cache_key(PostResult.resource_name, post_id),
                field_name="total_comments",
                delta=-1,
            ),
            CounterUpdate(
                counter_group=derive_hashmap_name(
                    UserResult.resource_name, "total_comments"
                ),
                cache_key=derive_cache_key(
                    UserResult.resource_name, access_token["sid"]
                ),
                field_name="total_comments",
                delta=-1,
            ),
        )
        intent_updates: tuple[IntentUpdate, ...] = (
            IntentUpdate(
                intent_name=create_intent_flag(
                    CommentResult.resource_name,
                    Action.DELETE,
                    str(access_token["sid"]),
                    str(comment_id),
                ),
                intent_flag=IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
                intent_id=intent_id,
            ),
        )
        deletion_event: Event = Event(
            name=EventName.COMMENT_DELETE,
            event_id=intent_id,
            created_at=time.time(),
            payload={"comment_id": comment_id},
            side_effects=EventSideEffects(
                counter_updates=counter_updates, intent_updates=intent_updates  # type: ignore[reportCallIssue]
            ),
        )

        await event_streamer.emit_user_event(deletion_event)
    return JSONResponse({"message": "Comment queued for deletion"}, 202)


@COMMENTS.post("/{comment_id}/votes")
async def vote_comment(
    post_id: int,
    comment_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    vote_model: VoteModel,
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    comment_repo: Annotated[CommentRepository, Depends(get_comment_repository)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    comment_cache_key: Final[str] = derive_cache_key(
        CommentResult.resource_name, comment_id
    )
    comment: CommentResult | None = await cache_manager.distributed_get_or_load(
        comment_cache_key, partial(comment_repo.get_comment, comment_id), CommentResult
    )

    if not comment:
        raise HTTPException(404, "Comment not found")
    intent: Final[IntentFlag] = (
        IntentFlag.RESOURCE_CREATION_PENDING_FLAG
        if vote_model.vote == 1
        else IntentFlag.RESOURCE_CREATION_PENDING_ALT_FLAG
    )
    delta: int = vote_model.vote

    conflict_message: str = (
        f"Comment already {'upvoted' if vote_model.vote == 1 else 'downvoted'}"
    )
    async with cache_manager.guard_action(
        access_token["sid"],
        comment_id,
        CommentResult.resource_name,
        Action.VOTE,
        conflicting_intent=intent,
        intent_conflict_message=conflict_message,
    ) as latest_intent:
        intent_id: Final[str] = uuid4().hex
        if not latest_intent:
            existing_vote: bool | None = await comment_repo.get_vote(
                post_id, access_token["sid"]
            )
            if (existing_vote == True and vote_model.vote == 1) or (
                existing_vote == False and vote_model.vote == -1
            ):
                await cache_manager.set_intent(
                    intent_id,
                    str(access_token["sid"]),
                    str(comment_id),
                    CommentVote.__tablename__,
                    Action.VOTE,
                    intent,
                )
                raise HTTPException(409, conflict_message)
            elif existing_vote:
                # Transitioning from upvote to downvote, or vice-versa
                delta *= 2

        counter_updates: tuple[CounterUpdate, ...] = (
            CounterUpdate(
                counter_group=derive_hashmap_name(CommentResult.resource_name, "score"),
                cache_key=comment_cache_key,
                field_name="score",
                delta=delta,
            ),
            CounterUpdate(
                counter_group=derive_hashmap_name(UserResult.resource_name, "aura"),
                cache_key=derive_cache_key(UserResult.resource_name, comment.author_id),
                field_name="aura",
                delta=delta,
            ),
        )
        intent_updates: tuple[IntentUpdate, ...] = (
            IntentUpdate(
                intent_name=create_intent_flag(
                    CommentResult.resource_name,
                    Action.VOTE,
                    str(access_token["sid"]),
                    str(comment_id),
                ),
                intent_flag=intent,
                intent_id=intent_id,
            ),
        )

        vote_event: Event = Event(
            name=EventName.COMMENT_VOTE,
            event_id=intent_id,
            created_at=time.time(),
            payload={
                "comment_id": comment_id,
                "user_id": access_token["sid"],
                "vote": True if vote_model.vote else False,
            },
            side_effects=EventSideEffects(
                counter_updates=counter_updates, intent_updates=intent_updates
            ),  # type: ignore[reportCallIssue]
        )

        await event_streamer.emit_user_event(vote_event)
    return JSONResponse({"message": "Voted"}, 202)


@COMMENTS.delete("/{comment_id}/votes")
async def unvote_comment(
    post_id: int,
    comment_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    comment_repo: Annotated[CommentRepository, Depends(get_comment_repository)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    comment_cache_key: Final[str] = derive_cache_key(
        CommentResult.resource_name, comment_id
    )
    delta: int = -1  # Initially assume upvote

    comment: CommentResult | None = await cache_manager.distributed_get_or_load(
        comment_cache_key, partial(comment_repo.get_comment, comment_id), CommentResult
    )
    if not comment:
        raise HTTPException(404, "Comment not found")

    conflict_message: str = "No vote casted on this comment"
    async with cache_manager.guard_action(
        access_token["sid"],
        comment_id,
        CommentResult.resource_name,
        Action.VOTE,
        conflicting_intent=IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
        intent_conflict_message=conflict_message,
    ) as latest_intent:
        intent_id: Final[str] = uuid4().hex
        if latest_intent == IntentFlag.RESOURCE_CREATION_PENDING_ALT_FLAG:  # Downvote
            delta = -1
        elif not latest_intent:
            existing_vote: bool | None = await comment_repo.get_vote(
                post_id, access_token["sid"]
            )
            if not existing_vote:
                await cache_manager.set_intent(
                    intent_id,
                    str(access_token["sid"]),
                    str(comment_id),
                    CommentVote.__tablename__,
                    Action.VOTE,
                    IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
                )
                raise HTTPException(409, conflict_message)
            elif existing_vote == False:  # downvote
                delta = -1

        counter_updates: tuple[CounterUpdate, ...] = (
            CounterUpdate(
                counter_group=derive_hashmap_name(CommentResult.resource_name, "score"),
                cache_key=comment_cache_key,
                field_name="score",
                delta=delta,
            ),
            CounterUpdate(
                counter_group=derive_hashmap_name(UserResult.resource_name, "aura"),
                cache_key=derive_cache_key(UserResult.resource_name, comment.author_id),
                field_name="aura",
                delta=delta,
            ),
        )
        intent_updates: tuple[IntentUpdate, ...] = (
            IntentUpdate(
                intent_name=create_intent_flag(
                    CommentResult.resource_name,
                    Action.UNVOTE,
                    str(access_token["sid"]),
                    str(comment_id),
                ),
                intent_flag=IntentFlag.RESOURCE_DELETION_PENDING_FLAG,
                intent_id=intent_id,
            ),
        )

        vote_event: Event = Event(
            name=EventName.COMMENT_UNVOTE,
            event_id=intent_id,
            created_at=time.time(),
            payload={
                "comment_id": comment_id,
                "user_id": access_token["sid"],
                "vote": delta,
            },
            side_effects=EventSideEffects(
                counter_updates=counter_updates, intent_updates=intent_updates
            ),  # type: ignore[reportCallIssue]
        )

        await event_streamer.emit_user_event(vote_event)
    return JSONResponse({"message": "Removed vote"}, 202)


@COMMENTS.delete("/{comment_id}/votes")
async def report_comment(
    post_id: int,
    comment_id: int,
    access_token: Annotated[StandardAccessTokenClaims, Depends(validate_access_token)],
    report_model: ReportModel,
    cache_manager: Annotated[CacheManager, Depends(get_cache_manager)],
    comment_repo: Annotated[CommentRepository, Depends(get_comment_repository)],
    event_streamer: Annotated[EventStreamer, Depends(get_event_streamer)],
) -> JSONResponse:
    comment_cache_key: Final[str] = derive_cache_key(
        CommentResult.resource_name, comment_id
    )
    comment: CommentResult | None = await cache_manager.distributed_get_or_load(
        comment_cache_key, partial(comment_repo.get_comment, comment_id), CommentResult
    )
    if not comment:
        raise HTTPException(404, "Comment not found")

    resource_name: str = NAME_SEPERATOR.join(
        (CommentResult.resource_name, report_model.tag)
    )

    conflict_message: str = f"Comment already reported for reason: {report_model.tag}"
    async with cache_manager.guard_action(
        access_token["sid"],
        comment_id,
        resource_name,
        Action.REPORT,
        conflicting_intent=IntentFlag.RESOURCE_CREATION_PENDING_FLAG,
        intent_conflict_message=conflict_message,
    ) as latest_intent:
        intent_id: Final[str] = uuid4().hex
        if not latest_intent:
            if await comment_repo.check_reported(
                comment_id, access_token["sid"], report_model.tag
            ):
                await cache_manager.set_intent(
                    intent_id,
                    str(access_token["sid"]),
                    str(comment_id),
                    resource_name,
                    Action.REPORT,
                    IntentFlag.RESOURCE_CREATION_PENDING_FLAG,
                )
                raise HTTPException(409, conflict_message)

        counter_updates: tuple[CounterUpdate, ...] = (
            CounterUpdate(
                counter_group=derive_hashmap_name(
                    CommentResult.resource_name, "reports"
                ),
                cache_key=comment_cache_key,
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
                    str(comment_id),
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
