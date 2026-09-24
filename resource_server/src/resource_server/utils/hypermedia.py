from collections.abc import Mapping, Sequence
from http import HTTPMethod
from typing import Any, Protocol

from auxillary.data_structures.enriched.hypermedia import (
    HypermediaResponse,
    HypermediaResponseSequence,
)
from auxillary.data_structures.enriched.link_builder import HypermediaLinkBuilder

__all__ = (
    "anime_hypermedia",
    "comment_hypermedia",
    "forum_hypermedia",
    "paginated_collection_hypermedia",
    "post_hypermedia",
    "single_link_hypermedia",
    "user_hypermedia",
)


class AnimeResource(Protocol):
    id_: int


class ForumResource(Protocol):
    id_: int
    anime: int


class PostResource(Protocol):
    id_: int
    forum_id: int
    author_username: str
    closed: bool


class CommentResource(Protocol):
    id_: int
    author_username: str
    parent_forum: int
    parent_post: int


class UserResource(Protocol):
    username: str


def _response_sequence(
    links: Sequence[HypermediaResponse],
) -> HypermediaResponseSequence:
    return HypermediaResponseSequence(links=list(links))


def single_link_hypermedia(
    builder: HypermediaLinkBuilder,
    route_name: str,
    rel: str,
    method: HTTPMethod = HTTPMethod.GET,
    *,
    query: dict[str, Any] | None = None,
    **path_parameters: Any,
) -> HypermediaResponseSequence:
    return _response_sequence(
        [builder.link(route_name, rel, method, query=query, **path_parameters)]
    )


def paginated_collection_hypermedia(
    builder: HypermediaLinkBuilder,
    route_name: str,
    *,
    path_parameters: Mapping[str, Any] | None = None,
    query: Mapping[str, Any] | None = None,
    next_cursor: str | None = None,
    cursor_parameter: str = "cursor",
    include_first: bool = True,
    related_links: Sequence[HypermediaResponse] = (),
) -> HypermediaResponseSequence:
    """Build navigation links for a cursor-paginated collection.

    ``query`` should contain filters that must survive navigation. A cursor in that
    mapping is removed from the ``first`` relation and replaced for ``next``.
    """

    path_parameters = path_parameters or {}
    navigation_query = dict(query or {})
    navigation_query.pop(cursor_parameter, None)

    links: list[HypermediaResponse] = [builder.link_self()]
    links.extend(related_links)

    if include_first:
        links.append(
            builder.link(
                route_name,
                "first",
                query=navigation_query,
                **path_parameters,
            )
        )

    if next_cursor is not None:
        links.append(
            builder.link(
                route_name,
                "next",
                query=navigation_query | {cursor_parameter: next_cursor},
                **path_parameters,
            )
        )

    return _response_sequence(links)


def anime_hypermedia(
    builder: HypermediaLinkBuilder,
    anime: AnimeResource,
    *,
    include_self: bool = True,
    subscription_state: bool | None = None,
) -> HypermediaResponseSequence:
    links: list[HypermediaResponse] = []

    if include_self:
        links.append(builder.link("get_anime", "self", anime_id=anime.id_))

    links.extend(
        (
            builder.link("get_animes", "collection"),
            builder.link("get_anime_forums", "forums", anime_id=anime.id_),
            builder.link("get_anime_genres", "genres"),
        )
    )

    if subscription_state is True:
        links.append(
            builder.link(
                "unsub_anime",
                "unsubscribe",
                HTTPMethod.DELETE,
                anime_id=anime.id_,
            )
        )
    elif subscription_state is False:
        links.append(
            builder.link(
                "sub_anime",
                "subscribe",
                HTTPMethod.POST,
                anime_id=anime.id_,
            )
        )

    return _response_sequence(links)


def forum_hypermedia(
    builder: HypermediaLinkBuilder,
    forum: ForumResource,
    *,
    include_self: bool = True,
    subscription_state: bool | None = None,
    can_create_post: bool = False,
    can_edit: bool = False,
    can_delete: bool = False,
    can_manage_admins: bool = False,
) -> HypermediaResponseSequence:
    links: list[HypermediaResponse] = []

    if include_self:
        links.append(builder.link("get_forum", "self", forum_id=forum.id_))

    links.extend(
        (
            builder.link("get_anime", "anime", anime_id=forum.anime),
            builder.link("get_forum_posts", "posts", forum_id=forum.id_),
            builder.link("get_forum_admins", "admins", forum_id=forum.id_),
        )
    )

    if subscription_state is True:
        links.append(
            builder.link(
                "unsubscribe_forum",
                "unsubscribe",
                HTTPMethod.DELETE,
                forum_id=forum.id_,
            )
        )
    elif subscription_state is False:
        links.append(
            builder.link(
                "subscribe_forum",
                "subscribe",
                HTTPMethod.POST,
                forum_id=forum.id_,
            )
        )

    if can_create_post:
        links.append(builder.link("create_post", "create-post", HTTPMethod.POST))
    if can_edit:
        links.append(
            builder.link(
                "edit_forum",
                "edit",
                HTTPMethod.PATCH,
                forum_id=forum.id_,
            )
        )
    if can_delete:
        links.append(
            builder.link(
                "delete_forum",
                "delete",
                HTTPMethod.DELETE,
                forum_id=forum.id_,
            )
        )
    if can_manage_admins:
        links.extend(
            (
                builder.link(
                    "add_admin",
                    "add-admin",
                    HTTPMethod.POST,
                    forum_id=forum.id_,
                ),
                builder.link(
                    "edit_admin_permissions",
                    "edit-admin",
                    HTTPMethod.PATCH,
                    forum_id=forum.id_,
                ),
                builder.link(
                    "remove_admin",
                    "remove-admin",
                    HTTPMethod.DELETE,
                    forum_id=forum.id_,
                ),
            )
        )

    return _response_sequence(links)


def post_hypermedia(
    builder: HypermediaLinkBuilder,
    post: PostResource,
    *,
    include_self: bool = True,
    can_edit: bool = False,
    can_delete: bool = False,
    can_comment: bool = False,
    has_vote: bool | None = None,
    is_saved: bool | None = None,
    can_report: bool = False,
) -> HypermediaResponseSequence:
    links: list[HypermediaResponse] = []

    if include_self:
        links.append(builder.link("get_post", "self", post_id=post.id_))

    links.extend(
        (
            builder.link("get_forum", "forum", forum_id=post.forum_id),
            builder.link("get_user", "author", username=post.author_username),
            builder.link("get_post_comments", "comments", post_id=post.id_),
        )
    )

    if can_edit:
        links.append(
            builder.link(
                "edit_post",
                "edit",
                HTTPMethod.PATCH,
                post_id=post.id_,
            )
        )
    if can_delete:
        links.append(
            builder.link(
                "delete_post",
                "delete",
                HTTPMethod.DELETE,
                post_id=post.id_,
            )
        )
    if can_comment and not post.closed:
        links.append(
            builder.link(
                "comment_on_post",
                "create-comment",
                HTTPMethod.POST,
                query={"post_id": post.id_},
            )
        )

    if has_vote is True:
        links.append(
            builder.link(
                "unvote_post",
                "remove-vote",
                HTTPMethod.DELETE,
                post_id=post.id_,
            )
        )
    elif has_vote is False:
        links.append(
            builder.link(
                "vote_post",
                "vote",
                HTTPMethod.POST,
                post_id=post.id_,
            )
        )

    if is_saved is True:
        links.append(
            builder.link(
                "unsave_post",
                "unsave",
                HTTPMethod.DELETE,
                post_id=post.id_,
            )
        )
    elif is_saved is False:
        links.append(
            builder.link(
                "save_post",
                "save",
                HTTPMethod.POST,
                post_id=post.id_,
            )
        )

    if can_report:
        links.append(
            builder.link(
                "report_post",
                "report",
                HTTPMethod.POST,
                post_id=post.id_,
            )
        )

    return _response_sequence(links)


def comment_hypermedia(
    builder: HypermediaLinkBuilder,
    comment: CommentResource,
    *,
    can_delete: bool = False,
    has_vote: bool | None = None,
) -> HypermediaResponseSequence:
    links: list[HypermediaResponse] = [
        builder.link("get_post", "post", post_id=comment.parent_post),
        builder.link("get_forum", "forum", forum_id=comment.parent_forum),
        builder.link("get_user", "author", username=comment.author_username),
    ]
    query = {"post_id": comment.parent_post}

    if can_delete:
        links.append(
            builder.link(
                "delete_comment",
                "delete",
                HTTPMethod.DELETE,
                query=query,
                comment_id=comment.id_,
            )
        )

    if has_vote is True:
        links.append(
            builder.link(
                "unvote_comment",
                "remove-vote",
                HTTPMethod.DELETE,
                query=query,
                comment_id=comment.id_,
            )
        )
    elif has_vote is False:
        links.append(
            builder.link(
                "vote_comment",
                "vote",
                HTTPMethod.POST,
                query=query,
                comment_id=comment.id_,
            )
        )

    return _response_sequence(links)


def user_hypermedia(
    builder: HypermediaLinkBuilder,
    user: UserResource,
    *,
    include_self: bool = True,
    self_managed: bool = False,
) -> HypermediaResponseSequence:
    links: list[HypermediaResponse] = []

    if include_self:
        links.append(builder.link("get_user", "self", username=user.username))

    links.extend(
        (
            builder.link("get_user_posts", "posts", username=user.username),
            builder.link("get_user_forums", "forums", username=user.username),
            builder.link("get_user_animes", "animes", username=user.username),
        )
    )

    if self_managed:
        links.extend(
            (
                builder.link(
                    "delete_user",
                    "delete-account",
                    HTTPMethod.DELETE,
                    username=user.username,
                ),
                builder.link(
                    "recover_password",
                    "recover-password",
                    HTTPMethod.POST,
                ),
            )
        )

    return _response_sequence(links)
