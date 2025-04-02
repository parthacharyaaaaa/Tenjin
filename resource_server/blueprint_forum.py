from auxillary.decorators import enforce_json

from resource_server.external_extensions import RedisInterface
from auxillary.utils import rediserialize

from resource_server.models import db, Forum, User
from sqlalchemy import select, update

from werkzeug.exceptions import BadRequest, NotFound
from flask import Blueprint, Response, g, jsonify
forum = Blueprint(__file__.split(".")[0], __file__.split(".")[0], url_prefix="/forums")

@forum.route("/", methods=["GET", "HEAD", "OPTIONS"])
def index():
    ...

@forum.route("/", methods=["POST", "OPTIONS"])
@enforce_json
def create_forum() -> tuple[Response, int]:
    try:
        userID: int = int(g.REQUEST_JSON.pop('user_id'))
        forumName: str = str(g.REQUEST_JSON.pop('forum_name')).strip()
        if not forumName:
            raise ValueError()
        forumAnimeID: int = int(g.REQUEST_JSON.pop('anime_id'))
        colorTheme: int = int(g.REQUEST_JSON.pop('color_theme', 1))
        description: str | None = None if 'desc' not in g.REQUEST_JSON else str(g.REQUEST_JSON.pop('desc')).strip()
    except KeyError:
        raise BadRequest("Mandatory details for forum creation missing")
    except (TypeError, ValueError):
        raise BadRequest("Malformmatted values provided for forum creation")

    user: User = db.session.execute(select(User).where((User.id == userID) & (User.deleted == False))).scalar_one_or_none()
    if not user:
        raise NotFound("No user with this ID exists")
    
    # All checks passed, push >:3
    newForum: Forum = Forum(forumName, forumAnimeID, colorTheme, description)
    try:
        print(newForum.__attrdict__())
        RedisInterface.xadd("INSERTIONS", rediserialize(newForum.__attrdict__()) | {'table' : Forum.__tablename__})
    except:
        cErr = ConnectionError()
        cErr.__setattr__("description", "An error occured while trying to create the forum. This is most likely an issue with our servers")
        raise cErr

    return jsonify({"message" : "Forum created succesfully"}), 202

@forum.route("/", methods = ["DELETE", "OPTIONS"])
def delete_forum() -> Response:
    ...

