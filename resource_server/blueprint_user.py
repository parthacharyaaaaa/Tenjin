'''Blueprint module for all actions related to resource: user (`users` in database)
### Note: All user related actions (account creation, User.last_login updation, and account deletion are done directly without query dispatching to Redis)
'''
from flask import Blueprint, Response, g, request, jsonify
user = Blueprint(__file__.split(".")[0], __file__.split(".")[0], url_prefix="/users")

from werkzeug.exceptions import BadRequest, Conflict, InternalServerError, NotFound, Unauthorized
from auxillary.decorators import enforce_json
from auxillary.utils import processUserInfo
from auxillary.utils import hash_password, verify_password
from sqlalchemy import select, insert, update
from sqlalchemy.exc import IntegrityError

from resource_server.models import db, User

from datetime import datetime

@user.route("/", methods=["POST", "OPTIONS"])
@enforce_json
def register() -> Response:
    print(g.REQUEST_JSON)
    if not (g.REQUEST_JSON.get('username') and
            g.REQUEST_JSON.get('password') and
            g.REQUEST_JSON.get('email')):
        raise BadRequest("Response requires username, password, and email")
    
    op, USER_DETAILS = processUserInfo(username=g.REQUEST_JSON["username"],
                                       email=g.REQUEST_JSON["email"],
                                       password=g.REQUEST_JSON["password"])
    if not op:
        raise BadRequest(USER_DETAILS.get("error"))

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
    if not (g.REQUEST_JSON.get("username") and
            g.REQUEST_JSON.get("password")):
        raise BadRequest("Requires username and password to be provided")
    
    OP, USER_DETAILS = processUserInfo(username=g.REQUEST_JSON['username'], password=g.REQUEST_JSON['password'])
    if not OP:
        raise BadRequest(USER_DETAILS.get("error"))
    user : User | None = db.session.execute(select(User).where(User.username == USER_DETAILS['username']).with_for_update()).scalar_one_or_none()
    if not user:
        raise NotFound("Requested user could not be found")
    
    try:
        db.session.execute(update(User).where(User.id == user.id).values(deleted=True, time_deleted=datetime.now()))
        db.session.commit()
    except:
        raise InternalServerError("Failed to perform account deletion, please try again. If the issue persists, please raise a ticket")
    
    #TODO: Add logic for purging user's JWTs from auth server
    return jsonify({"message" : "account deleted succesfully", "username" : user.username, "time_deleted" : user.time_deleted}), 203

@user.route("/", methods=["GET", "HEAD", "OPTIONS"])
def get_users() -> Response:
    ...

@user.route("/login", methods=["POST", "OPTIONS"])
@enforce_json
def login() -> Response:
    if not(g.REQUEST_JSON.get('identity') and g.REQUEST_JSON.get('password')):
        raise BadRequest("Login requires email/username and password to be provided")
    
    identity : str = g.REQUEST_JSON['identity'].strip()
    if not 5 < len(identity) < 320:
        raise BadRequest("Provided username/email must be between 5 and 320 characters long")

    isEmail = False
    if "@" in identity:
        query = [User.email == identity.lower()]
        isEmail = True
    else:
        query = [User.username == identity]

    user : User | None = db.session.execute(select(User).where(*query).with_for_update()).scalar_one_or_none()
    if not user:
        raise NotFound(f"No user witb {'email' if isEmail else 'username'} could be found")
    if not verify_password(g.REQUEST_JSON['password'], user.pw_hash, user.pw_salt):
        raise Unauthorized("Incorrect password")
    
    epoch = datetime.now()
    db.session.execute(update(User).where(User.id == user.id).values(last_login = epoch))
    db.session.commit()
    # Communicate with auth server
    
    response = jsonify(user.__json_like__())
    # response.cookies.update(tokenPair)
    return response, 200