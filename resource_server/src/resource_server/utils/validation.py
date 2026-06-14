from typing import Any

from resource_server.models.requests import PostAmendmentModel
from resource_server.repositories.posts import PostResult


def validate_duplicate_amendment_contents(
    model: PostAmendmentModel, post: PostResult
) -> dict[str, Any]:
    error_dict: dict[str, Any] = {}
    if post.closed and model.closed:
        error_dict["closed"] = "Post already closed"
    if post.title == model.title:
        error_dict["title"] = "New title identical to existing title"
    if not model.title and model.closed is None and model.body == post.body_text:
        error_dict["description"] = "New description identical to existing description"

    return error_dict
