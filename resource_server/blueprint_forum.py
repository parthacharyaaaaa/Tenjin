from auxillary.decorators import enforce_json, token_required

from resource_server.external_extensions import RedisInterface
from auxillary.utils import rediserialize, genericDBFetchException

from resource_server.models import db, Forum, User, ForumAdmin, Post, Anime, ForumSubscription
from sqlalchemy import select, update, insert, desc
from sqlalchemy.exc import SQLAlchemyError

from types import MappingProxyType
from resource_server.external_extensions import hset_with_ttl
from datetime import datetime, timedelta
import base64
import binascii
from typing import Any

from werkzeug.exceptions import BadRequest, NotFound, Forbidden, Conflict
from flask import Blueprint, Response, g, jsonify, request, current_app
forum = Blueprint(__file__.split(".")[0], __file__.split(".")[0], url_prefix="/forums")

TIMEFRAMES: MappingProxyType = MappingProxyType({0 : lambda dt : dt - timedelta(hours=1),
                                                 1 : lambda dt : dt - timedelta(days=1),
                                                 2 : lambda dt : dt - timedelta(weeks=1),
                                                 3 : lambda dt : dt - timedelta(days=30),
                                                 4 : lambda dt : dt - timedelta(days=364),
                                                 5 : lambda _ : datetime.min})

@forum.route("/<int:forum_id>/posts")
def get_forum_posts(forum_id: int) -> tuple[Response, int]:
    try:
        rawCursor = request.args.get('cursor', '0').strip()
        if rawCursor == '0':
            cursor = 0
            init: bool = True
        else:
            init: bool = False
            cursor = int(base64.b64decode(rawCursor).decode())

        sortOption: str = request.args.get('sort', '0').strip()
        if not sortOption.isnumeric() or sortOption not in ('0', '1'):
            sortOption = '0'
        
        timeFrame: str = request.args.get('timeframe', '5').strip()
        if not timeFrame.isnumeric() or not (0 <= int(timeFrame) <= 5):
            timeFrame = 5
        else:
            timeFrame = int(timeFrame)
        
    except (ValueError, TypeError, binascii.Error):
            raise BadRequest("Failed to load more posts. Please refresh this page")
    
    whereClause = (Post.forum_id == forum_id) & (Post.time_posted >= TIMEFRAMES[timeFrame](datetime.now()))
    if not init:
        whereClause &= (Post.id < cursor)

    query = (select(Post, User.username)
             .join(User, Post.author_id == User.id)
             .where(whereClause)
             .order_by(desc(Post.score if sortOption == '1' else Post.time_posted))
             .limit(6))
    
    nextPosts: list[Post] = db.session.execute(query).all()
    if not nextPosts:
        return jsonify({'posts' : None, 'cursor' : cursor}), 200
    end = False
    if len(nextPosts) != 6:
        end = True
    postsJSON: list[dict[str, Any]] = [post.__json_like__() | {'username' : username} for post, username in nextPosts]
    cursor = base64.b64encode(str(nextPosts[-1][0].id).encode('utf-8')).decode()

    return jsonify({'posts' : postsJSON, 'cursor' : cursor, 'end' : end}), 200

@forum.route("/", methods=["POST"])
@enforce_json
@token_required
def create_forum() -> tuple[Response, int]:
    try:
        userID: int = g.decodedToken['sid']
        forumName: str = str(g.REQUEST_JSON.pop('forum_name')).strip()
        if not forumName:
            raise ValueError()
        forumAnimeID: int = int(g.REQUEST_JSON.pop('anime_id'))
        description: str | None = None if 'desc' not in g.REQUEST_JSON else str(g.REQUEST_JSON.pop('desc')).strip()
    except KeyError:
        raise BadRequest("Mandatory details for forum creation missing")
    except (TypeError, ValueError):
        raise BadRequest("Malformmatted values provided for forum creation")
    
    # 1: Consult cache
    cacheKey: str = f'forum:{forumName}:{forumAnimeID}'
    try:
        exists: bool = bool(RedisInterface.hget(cacheKey))
        if exists:
            raise Conflict("A forum with this name for this anime already exists")
    except: ...
    
    # 2: Fallback to DB
    try:
        forumExisting: Forum = db.session.execute(select(Forum)
                                                  .where((Forum._name == forumName) & (Forum.anime == forumAnimeID))
                                                  ).scalar_one_or_none()
        if forumExisting:
            raise Conflict("A forum with this name for this anime already exists")
        anime: Anime = db.session.execute(select(Anime)
                                          .where(Anime.id == forumAnimeID)
                                          ).scalar_one_or_none()
        if not anime:
            # Set 404 mapping ephemerally for this anime ID
            hset_with_ttl(RedisInterface, f'anime:{forumAnimeID}', {'__NF__' : -1}, current_app.config['REDIS_TTL_EPHEMERAL'])
            raise NotFound(f"No anime with ID: {forumAnimeID} could be found")
    except SQLAlchemyError: genericDBFetchException()

    # All checks passed, push >:3
    try:
        newForum: Forum = db.session.execute(insert(Forum)
                                 .values(_name = forumName, anime=forumAnimeID, description=description, created_at=datetime.now())
                                 .returning(Forum)).scalar_one_or_none()
        db.session.commit()
        db.session.execute(insert(ForumAdmin)
                           .values(forum_id=newForum.id, user_id=userID, role='owner'))
        db.session.commit()
    except SQLAlchemyError as sqlErr:
        db.session.rollback()
        sqlErr.__setattr__("description", "An error occured while trying to create the forum. This is most likely an issue with our servers")
        raise sqlErr

    hset_with_ttl(RedisInterface, cacheKey, rediserialize(newForum.__json_like__()), current_app.config['REDIS_TTL_STRONG'])
    return jsonify({"message" : "Forum created succesfully"}), 202

@forum.route("/<int:forum_id>", methods = ["DELETE"])
@token_required
def delete_forum(forum_id: int) -> Response:
    try:
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

@forum.route("/<int:forum_id>/admins", methods=['POST'])
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

@forum.route("/<int:forum_id>/admins", methods=['DELETE'])
@enforce_json
@token_required
def remove_admin(forum_id: int) -> tuple[Response, int]:
    targetAdminID: int = g.REQUEST_JSON.pop('user_id', None)
    if not targetAdminID:
        raise BadRequest('Missing user id for new admin')

    # Request details valid at a surface level.
    try:        
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


@forum.route("/<int:forum_id>/subscribe", methods=['PATCH'])
@token_required
def subscribe_forum(forum_id: int) -> tuple[Response, int]:
    try:
        subbedForum: ForumSubscription = db.session.execute(select(ForumSubscription)
                                                            .where((ForumSubscription.forum_id == forum_id) & (ForumSubscription.user_id == g.decodedToken['sid']))).scalar_one_or_none()
        if subbedForum:
            print(1)
            return jsonify({'message' : 'Already subscribed to this forum'}), 204
        _forum = db.session.execute(select(Forum).where(Forum.id == forum_id)).scalar_one()

    except SQLAlchemyError: genericDBFetchException()
    except KeyError: raise BadRequest('Invalid token, please login again')

    subCounterKey: str = RedisInterface.hget(f'{Forum.__tablename__}:subscribers', forum_id)
    RedisInterface.xadd("WEAK_INSERTIONS", {'user_id' : g.decodedToken['sid'], 'forum_id' : forum_id, 'table' : ForumSubscription.__tablename__})
    if subCounterKey:
        RedisInterface.incr(subCounterKey)
        return jsonify({'message' : 'subscribed!'}), 200
    
    subCounterKey = f'forum:{forum_id}:subscribers' 
    op = RedisInterface.set(subCounterKey, _forum.subscribers+1, nx=True)
    if not op:
        RedisInterface.incr(subCounterKey)
        return jsonify({'message' : 'subscribed!'}), 200
    
    RedisInterface.hset(f"{Forum.__tablename__}:subscribers", forum_id, subCounterKey)
    return jsonify({'message' : 'subscribed!'}), 200


@forum.route("/<int:forum_id>/unsubscribe", methods=['PATCH'])
@token_required
def unsubscribe_forum(forum_id: int) -> tuple[Response, int]:
    try:
        subbedForum: ForumSubscription = db.session.execute(select(ForumSubscription)
                                                            .where((ForumSubscription.forum_id == forum_id) & (ForumSubscription.user_id == g.decodedToken['sid']))).scalar_one_or_none()
        if not subbedForum:
            return jsonify({'message' : 'You have not subscribed to this forum'}), 204
        _forum = db.session.execute(select(Forum).where(Forum.id == forum_id)).scalar_one()
        
    except SQLAlchemyError: genericDBFetchException()
    except KeyError: raise BadRequest('Invalid token, please login again')

    subCounterKey: str = RedisInterface.hget(f'{Forum.__tablename__}:subscribers', forum_id)
    RedisInterface.xadd("WEAK_DELETIONS", {'user_id' : g.decodedToken['sid'], 'forum_id' : forum_id, 'table' : ForumSubscription.__tablename__})
    if subCounterKey:
        RedisInterface.decr(subCounterKey)
        return jsonify({'message' : 'unsubscribed!'}), 200
    
    subCounterKey = f'forum:{forum_id}:subscribers' 
    op = RedisInterface.set(subCounterKey, _forum.subscribers-1, nx=True)
    if not op:
        RedisInterface.decr(subCounterKey)
        return jsonify({'message' : 'unsubscribed!'}), 200
    
    RedisInterface.hset(f"{Forum.__tablename__}:subscribers", forum_id, subCounterKey)
    return jsonify({'message' : 'unsubscribed!'}), 200

@forum.route("/<int:forum_id>/edit", methods=["PATCH"])
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

    return jsonify({'message' : 'Edited forum'}), 200

@forum.route("/<int:forum_id>/highlight-post", methods=['PATCH'])
@token_required
def add_highlight_post(forum_id: int) -> tuple[Response, int]:
    postID: str = request.args.get('post')
    if not postID:
        raise BadRequest("No post specified")
    if not postID.isnumeric():
        raise BadRequest("Invalid post specified")
    
    postID: int = int(postID)
    try:
        userAdminRole: str = db.session.execute(select(ForumAdmin).where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.decodedToken['sid']))).scalar_one_or_none()
        if not userAdminRole or userAdminRole not in ('super', 'owner'):
            raise Forbidden('You do not have access rights to edit this forum')

        requestedPostID: int = db.session.execute(select(Post.id).where(Post.id == postID)).scalar_one_or_none()
        if not requestedPostID:
            raise NotFound("This post could not be found")
        
    except SQLAlchemyError: genericDBFetchException()
    except KeyError: raise BadRequest('Invalid token, missing mandatory field: sid. Please login again')

    try:
        forum: Forum = db.session.execute(select(Forum).where(Forum.id == forum_id).with_for_update(nowait=True)).scalar_one_or_none()
        for idx, highlight_post in enumerate((forum.highlight_post_1, forum.highlight_post_2, forum.highlight_post_3), 1):
            if postID == highlight_post:
                raise Conflict("This post is already highlighted in this forum")
            if not highlight_post:
                db.session.execute(update(Forum).where(Forum.id == forum_id).values(**{f'highlight_post_{idx}' : postID}))
                db.session.commit()
                break
        else:
            raise Conflict("This forum already has 3 highlighted post. Please remove one of them to accomodate this one")
        
    except SQLAlchemyError as e:
        e.__setattr__('description', 'An error occured when adding this highlighted post. Please try again later')
        raise e
    
    return jsonify({'message' : 'Post highlighted'}), 200

@forum.route("/<int:forum_id>/highlight-post", methods=['DELETE'])
@token_required
def remove_highlight_post(forum_id: int) -> tuple[Response, int]:
    postID: str = request.args.get('post')
    if not postID:
        raise BadRequest("No post specified")
    if not postID.isnumeric():
        raise BadRequest("Invalid post specified")
    
    postID: int = int(postID)
    try:
        userAdminRole: str = db.session.execute(select(ForumAdmin).where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.decodedToken['sid']))).scalar_one_or_none()
        if not userAdminRole or userAdminRole not in ('super', 'owner'):
            raise Forbidden('You do not have access rights to edit this forum')

        requestedPostID: int = db.session.execute(select(Post.id).where(Post.id == postID)).scalar_one_or_none()
        if not requestedPostID:
            raise NotFound("This post could not be found")
        
    except SQLAlchemyError: genericDBFetchException()
    except KeyError: raise BadRequest('Invalid token, missing mandatory field: sid. Please login again')

    try:
        forum: Forum = db.session.execute(select(Forum).where(Forum.id == forum_id).with_for_update(nowait=True)).scalar_one_or_none()
        idx: int = [forum.highlight_post_1, forum.highlight_post_2, forum.highlight_post_3].index(postID) + 1

        db.session.execute(update(Forum).where(Forum.id == forum_id).values(**{f'highlight_post_{idx}' : None}))
        db.session.commit()        
    except ValueError: raise BadRequest("This post is not highlighted")
    except SQLAlchemyError as e:
        e.__setattr__('description', 'An error occured when removing this highlighted post. Please try again later')
        raise e
    
    return jsonify({'message' : 'Post removed from forum highlights'}), 200

@forum.route('<int:forum_id>/posts', methods=['GET'])
def get_posts(forum_id: int) -> tuple[Response, int]:
    try:
        rawCursor = request.args.get('cursor').strip()
        if rawCursor == '0':
            cursor = 0
        elif not rawCursor:
            raise BadRequest("Failed to load more posts. Please refresh this page")
        else:
            cursor = int(base64.b64decode(rawCursor).decode())
        
        sortOption = request.args.get('sort', 0)
        if sortOption != 1:
            sortOption = 0          # Either top or newest, if anything else is given fall back to newest
    except (ValueError, TypeError):
            raise BadRequest("Failed to load more posts. Please refresh this page")

    try:
        nextPosts: list[Post] = db.session.execute(select(Post)
                                                   .where((Post.id > cursor) & (Post.forum_id == forum_id))
                                                    .order_by(Post.time_posted if not sortOption else Post.score)
                                                   .limit(5)
                                                   ).scalars().all()
        if not nextPosts:
            return jsonify({'posts' : None, 'cursor' : None}), 204
        
        authorsMap: dict[int, str] = dict(db.session.execute(select(User.id, User.username).where(User.id.in_([post.author_id for post in nextPosts]))).all())
    except SQLAlchemyError: genericDBFetchException()


    cursor = base64.b64encode(str(nextPosts[-1].id).encode('utf-8')).decode()
    nextPosts: list[dict[str, Any]] = [post.__json_like__() | {'author' : authorsMap[post.author_id]} for post in nextPosts]
    return jsonify({'posts' : nextPosts, 'cursor' : cursor}), 200