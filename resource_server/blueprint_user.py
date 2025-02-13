from flask import Blueprint, Response
user = Blueprint(__file__.split(".")[0], __file__.split(".")[0], url_prefix="/users")

@user.route("/login", methods=["POST", "OPTIONS"])
def login() -> Response:
    ...

@user.route("/", methods=["POST", "OPTIONS"])
def register() -> Response:
    ...

@user.route("/", methods=["DELETE"])
def delete_user() -> Response:
    ...

@user.route("/", methods=["GET", "HEAD", "OPTIONS"])
def get_users() -> Response:
    ...

@user.route("/logout", methods=["DELETE", "OPTIONS"])
def logout() -> Response:
    ...