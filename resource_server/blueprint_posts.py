from flask import Blueprint, Response
post = Blueprint("post", "post", url_prefix="/posts")

@post.route("/", methods=["POST", "OPTIONS"])
def create_post() -> Response:
    ...

@post.route("/<int:post_id>", methods=["GET", "OPTIONS"])
def get_post(post_id : int) -> Response:
    ...

@post.route("/<int:post_id>", methods=["PATCH", "OPTIONS"])
def edit_post(post_id : int) -> Response:
    ...

@post.route("/<int:post_id>", methods=["DELETE", "OPTIONS"])
def delete_post(post_id : int) -> Response:
    ...
