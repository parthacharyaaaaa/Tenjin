'''Blueprint module for all actions related to resource: user (`users` in database)'''
from flask import Blueprint, Response, g, request, jsonify
user = Blueprint(__file__.split(".")[0], __file__.split(".")[0], url_prefix="/users")

from werkzeug.exceptions import BadRequest, Conflict, InternalServerError
from auxillary.decorators import enforce_json
from auxillary.utils import processUserInfo
from auxillary.utils import hash_password, verify_password
from sqlalchemy import select, insert
from sqlalchemy.exc import IntegrityError

from resource_server import db
from resource_server.models import User

@enforce_json
@user.route("/", methods=["POST", "OPTIONS"])
def register() -> Response:
    if not (g.REQUEST_JSON.get('username').strip() and
            g.REQUEST_JSON.get('password').strip() and
            g.REQUEST_JSON.get('email').strip()):
        raise BadRequest("Response requires username, password, and email")
    
    op, USER_DETAILS = processUserInfo(g.REQUEST_JSON["username"], g.REQUEST_JSON["email"], g.REQUEST_JSON["password"])
    if not op:
        raise BadRequest(list(USER_DETAILS.values())[0])

    response_kwargs = {}
    if g.REQUEST_JSON.get("alias"):
        try:
            if not (5 < len(g.REQUEST_JSON["alias"].strip()) < 16):
                response_kwargs["alias_error"] = "Alias length should be in range 5 and 16"
        finally:
            g.REQUEST_JSON["alias"] = None

    if g.REQUEST_JSON.get("pfp"):
        try:
            if not (1 < g.REQUEST_JSON["pfp"] < 20):
                response_kwargs["pfp_error"] = "Invalid pfp selected"
        finally:
            g.REQUEST_JSON["pfp"] = None

    if db.session.execute(select(User).where((User.username == USER_DETAILS["username"]) | (User.email == USER_DETAILS["email"]))).scalar_one_or_none():
        conflict = Conflict("An account with this username or email address already exists")
        conflict.__setattr__("kwargs", response_kwargs)
        raise Conflict
    
    # All checks passed, user creation good to go
    passwordHash, passwordSalt = hash_password(USER_DETAILS.pop("password"))

    try:
        db.session.execute(insert(User).values(email=USER_DETAILS["email"],
                                               username=USER_DETAILS["username"],
                                               pw_hash=passwordHash,
                                               pw_salt=passwordSalt,
                                               pfp=g.REQUEST_JSON["pfp"],
                                               _alias=g.REQUEST_JSON["alias"]))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        conflict = Conflict("An account with this username/email was made at the same time as your request. Tough luck :(")
        conflict.__setattr__("kwargs", response_kwargs)
        raise conflict
    except:
        db.session.rollback()
        raise InternalServerError("An error occured with our database service")
    
    return jsonify({"message" : "Account created", "username" : USER_DETAILS["username"], "email" : USER_DETAILS["email"], "alias" : g.REQUEST_JSON.get("alias"), **response_kwargs}), 201

    
@user.route("/", methods=["DELETE"])
def delete_user() -> Response:
    ...

@user.route("/", methods=["GET", "HEAD", "OPTIONS"])
def get_users() -> Response:
    ...

@user.route("/login", methods=["POST", "OPTIONS"])
def login() -> Response:
    ...

@user.route("/logout", methods=["DELETE", "OPTIONS"])
def logout() -> Response:
    ...