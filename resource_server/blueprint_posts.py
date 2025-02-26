from flask import Blueprint, Response, jsonify
post = Blueprint("post", "post", url_prefix="/posts")

from sqlalchemy import select
from resource_server.models import db, Post

from werkzeug.exceptions import NotFound

@post.route("/", methods=["POST", "OPTIONS"])
def create_post() -> Response:
    ...

@post.route("/<int:post_id>", methods=["GET", "OPTIONS"])
def get_post(post_id : int) -> Response:
    post : Post | None = db.session.execute(select(Post).where(Post.id == post_id)).scalar_one_or_none()
    if not post:
        raise NotFound(f"Post with id {post_id} could not be found :(")
    return jsonify(post.__json_like__()), 200


@post.route("/<int:post_id>", methods=["PATCH", "OPTIONS"])
def edit_post(post_id : int) -> Response:
    ...

@post.route("/<int:post_id>", methods=["DELETE", "OPTIONS"])
def delete_post(post_id : int) -> Response:
    ...
