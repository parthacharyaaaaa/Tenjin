from auxillary.decorators import enforce_json, token_required

from resource_server.external_extensions import RedisInterface
from auxillary.utils import rediserialize, genericDBFetchException

from resource_server.models import db, Forum, User, ForumAdmin
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from datetime import datetime

from werkzeug.exceptions import BadRequest, NotFound, Forbidden
from flask import Blueprint, Response, g, jsonify
forum = Blueprint(__file__.split(".")[0], __file__.split(".")[0], url_prefix="/forums")

@forum.route("/", methods=["GET", "HEAD", "OPTIONS"])
def index():
    ...

@forum.route("/", methods=["POST", "OPTIONS"])
@enforce_json
@token_required
def create_forum() -> tuple[Response, int]:
    try:
        userID: int = g.decodedToken['sid']
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
    
    # All checks passed, push >:3
    newForum: Forum = Forum(forumName, forumAnimeID, colorTheme, description)
    try:
        db.session.add(newForum)
        db.session.flush()
        RedisInterface.xadd("INSERTIONS", rediserialize(newForum.__attrdict__()) | {'table' : Forum.__tablename__})
        RedisInterface.xadd("WEAK_INSERTIONS", {'table' : ForumAdmin.__tablename__, 'forum_id' : newForum.id, 'user_id' : userID, 'role' : 'owner'})
    except:
        db.session.rollback()
        cErr = ConnectionError()
        cErr.__setattr__("description", "An error occured while trying to create the forum. This is most likely an issue with our servers")
        raise cErr

    return jsonify({"message" : "Forum created succesfully"}), 202

@forum.route("/<int:forum_id>", methods = ["DELETE", "OPTIONS"])
@token_required
def delete_forum(forum_id: int) -> Response:
    try:
        # Check to see if forum exists
        forum: Forum = db.session.execute(select(Forum).where(Forum.id == forum_id).with_for_update()).scalar_one_or_none()
        if not forum:
            raise NotFound(f"Failed to find a forum with this id ({forum_id})")
        
        # Check to see if user is owner or superuser
        userAdmin: ForumAdmin = db.session.execute(select(ForumAdmin).where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.decodedToken['sid']))).scalar_one_or_none()
        if not userAdmin or userAdmin.role != 'owner' and userAdmin.role != 'super':
            raise Forbidden("You do not have the necessary permissions to delete this forum")
        
    except SQLAlchemyError: raise genericDBFetchException()
    except KeyError: raise BadRequest("Missing mandatory field 'sid', please login again")

    # Request carries the necessary permissions to delete this forum
    try:
        db.session.execute(update(Forum).where(Forum.id == forum_id).values(deleted=True, time_deleted=datetime.now()))
        db.session.commit()
    except: 
        exc: Exception = Exception()
        exc.__setattr__('description', 'Failed to delete this forum. Please try again later')
        raise exc
    
    return jsonify({'message' : 'forum deleted'}), 200