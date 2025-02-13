from flask import Blueprint, Response
forum = Blueprint(__file__.split(".")[0], __file__.split(".")[0], url_prefix="/animes")

@forum.route("/", methods=["GET", "HEAD", "OPTIONS"])
def index():
    ...

@forum.route("/", methods=["POST", "OPTIONS"])
def create_forum() -> Response:
    ...

@forum.route("/", methods = ["DELETE", "OPTIONS"])
def delete_forum() -> Response:
    ...

