from auxillary.decorators import enforce_json, token_required

from resource_server.external_extensions import RedisInterface
from auxillary.utils import rediserialize, genericDBFetchException

from resource_server.models import db, Forum, User, ForumAdmin
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from datetime import datetime

from werkzeug.exceptions import BadRequest, NotFound, Forbidden, Conflict
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

@forum.route("/<int:forum_id>/admins", methods=['POST', 'OPTIONS'])
@enforce_json
@token_required
def add_admin(forum_id: int) -> tuple[Response, int]:
    newAdminID: int = g.REQUEST_JSON.pop('user_id', None)
    if not newAdminID:
        raise BadRequest('Missing user id for new admin')
    if g.decodedToken['sid'] == newAdminID:
        raise Conflict("You are already an admin in this forum")
    newAdminRole: str = g.REQUEST_JSON.pop('role', 'admin')
    if newAdminRole not in ('admin', 'super'):
        raise BadRequest("Forum administrators can only have 2 roles: admin and super")
    # Request details valid at a surface level.
    try:
        # Check if forum exists
        forum: Forum = db.session.execute(select(Forum).where(Forum.id == forum_id).with_for_update(nowait=True)).scalar_one_or_none()
        if not forum:
            raise NotFound(f'No forum with id {forum_id} exists')
        
        # Check to see if new admin actually exists is users table
        newAdmin: int = db.session.execute(select(User.id).where(User.id == newAdminID)).scalar_one_or_none()
        if not newAdmin:
            raise NotFound('No user with this user id was found')
        
        # Check if user has necessary permissions
        userAdmin: ForumAdmin = db.session.execute(select(ForumAdmin).where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.decodedToken['sid']))).scalar_one_or_none()
        if not userAdmin or userAdmin.role not in ('admin', 'super'):
            raise Forbidden(f"You do not have the necessary permissions to add admins to: {forum._name}")
        
    except SQLAlchemyError: genericDBFetchException()
    except KeyError: raise BadRequest('Mandatory claim "sid" missing in token. Please login again')

    # Add WE entry in advance
    RedisInterface.xadd('WEAK_INSERTIONS', {'table' : ForumAdmin.__tablename__, 'forum_id' : forum_id, 'user_id' : newAdminID, 'role' : newAdminRole})

    # Increment admin counter
    adminCounterKey: str = RedisInterface.hget(f'{Forum.__tablename__}:admins', forum_id)
    if adminCounterKey:
        # Counter exists, increment and finish
        RedisInterface.incr(adminCounterKey)
        return jsonify({"message" : "Added new admin", "userID" : newAdminID, "role" : newAdminRole}), 202
    
    # Counter does not exist yet
    adminCounterKey: str = f"forum:{forum_id}:admins"
    fAdminCount: int = db.session.execute(select(Forum.admin_count).where(Forum.id == forum_id)).scalar_one()

    op = RedisInterface.set(adminCounterKey, fAdminCount+1, nx=True)
    if not op:
        # Other Flask worker already made a counter while this request was being processed, update counter and end
        RedisInterface.incr(adminCounterKey)
        return jsonify({"message" : "Added new admin", "userID" : newAdminID, "role" : newAdminRole}), 202
        
    RedisInterface.hset(f'{Forum.__tablename__}:admins', forum_id, adminCounterKey)
    return jsonify({"message" : "Added new admin", "userID" : newAdminID, "role" : newAdminRole}), 202

@forum.route("/<int:forum_id>/admins", methods=['DELETE', 'OPTIONS'])
@enforce_json
@token_required
def remove_admin(forum_id: int) -> tuple[Response, int]:
    targetAdminID: int = g.REQUEST_JSON.pop('user_id', None)
    if not targetAdminID:
        raise BadRequest('Missing user id for new admin')

    # Request details valid at a surface level.
    try:
        # Check if forum exists
        forum: Forum = db.session.execute(select(Forum).where(Forum.id == forum_id).with_for_update(nowait=True)).scalar_one_or_none()
        if not forum:
            raise NotFound(f'No forum with id {forum_id} exists')
        
        # Check to see if given user is actually an admin
        targetAdminRole: str = db.session.execute(select(ForumAdmin.role).where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == targetAdminID))).scalar_one_or_none()
        if not targetAdminRole:
            raise NotFound("This user is not an admin")
        
        # Check if user has necessary permissions
        userAdminRole: str = db.session.execute(select(ForumAdmin.role).where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.decodedToken['sid']))).scalar_one_or_none()
        rolePriority: dict = {'owner' : 2, 'super' : 1} #NOTE: Have a proper enum here later

        if not userAdminRole or rolePriority[userAdminRole] <= rolePriority[targetAdminRole]:
            raise Forbidden(f"You do not have the necessary permissions to remove this admin from: {forum._name}")
        
    except SQLAlchemyError: genericDBFetchException()
    except KeyError: raise BadRequest('Mandatory claim "sid" missing in token. Please login again')

    # Add WE entry in advance
    RedisInterface.xadd('WEAK_DELETIONS', {'table' : ForumAdmin.__tablename__, 'forum_id' : forum_id, 'user_id' : targetAdminID})

    # Decrement admin counter
    adminCounterKey: str = RedisInterface.hget(f'{Forum.__tablename__}:admins', forum_id)
    if adminCounterKey:
        # Counter exists, decrement and finish
        RedisInterface.decr(adminCounterKey)
        return jsonify({"message" : "Removed admin", "userID" : targetAdminID}), 202
    
    # Counter does not exist yet
    adminCounterKey: str = f"forum:{forum_id}:admins"
    fAdminCount: int = db.session.execute(select(Forum.admin_count).where(Forum.id == forum_id)).scalar_one()

    op = RedisInterface.set(adminCounterKey, fAdminCount-1, nx=True)
    if not op:
        # Other Flask worker already made a counter while this request was being processed, update counter and end
        RedisInterface.decr(adminCounterKey)
        return jsonify({"message" : "Removed admin", "userID" : targetAdminID}), 202
        
    RedisInterface.hset(f'{Forum.__tablename__}:admins', forum_id, adminCounterKey)
    return jsonify({"message" : "Removed admin", "userID" : targetAdminID}), 202

@forum.route("/<int:forum_id>/edit", methods=["PATCH", "OPTIONS"])
@enforce_json
@token_required
def edit_forum(forum_id: int) -> tuple[Response, int]:
    colorTheme: str | int = g.REQUEST_JSON.get('color_theme')
    if colorTheme and isinstance(colorTheme, str):
         if not colorTheme.isnumeric():
            raise BadRequest("Invalid color theme")
         colorTheme = int(colorTheme)
        
    description: str = g.REQUEST_JSON.pop('descriptions')

    if not (colorTheme or description):
        raise BadRequest("No changes provided")
    
    try:
        # Ensure user has access rights for this action
        userRole: str = db.session.execute(select(ForumAdmin.role).where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.decodedToken['sid']))).scalar_one_or_none()
        if not userRole or userRole != 'super' or userRole != 'owner':
            raise Forbidden('You do not have access rights to edit this forum')
        
        # Lock forum
        forum: Forum = db.session.execute(select(Forum).where(Forum.id == forum_id).with_for_update(nowait=True)).scalar_one_or_none()
    except SQLAlchemyError: genericDBFetchException()
    except KeyError: raise BadRequest('Invalid token, missing mandatory field: sid. Please login again')

    updateClauses: dict = {}
    if colorTheme:
        updateClauses['color_theme'] = colorTheme
    if description:
        updateClauses['description'] = description.strip()
    
    try:
        db.session.execute(update(Forum).where(Forum.id == forum_id).values(**updateClauses))
        db.session.commit()
    except SQLAlchemyError as e:
        e.__setattr__('description', 'Failed to update this forum. Please try again later')
        raise e