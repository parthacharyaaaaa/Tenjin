from auxillary.decorators import enforce_json

from auxillary.utils import rediserialize, genericDBFetchException, consult_cache

from resource_server.models import db, Forum, User, ForumAdmin, Post, Anime, ForumSubscription, AdminRoles
from resource_server.resource_decorators import token_required, pass_user_details
from resource_server.resource_auxillary import update_global_counter, fetch_global_counters
from resource_server.external_extensions import RedisInterface, hset_with_ttl
from sqlalchemy import select, update, insert, desc
from sqlalchemy.exc import SQLAlchemyError

from typing import Any
from types import MappingProxyType
from datetime import datetime, timedelta
from redis.exceptions import RedisError
import base64
import binascii

from werkzeug.exceptions import BadRequest, NotFound, Forbidden, Conflict, Unauthorized, InternalServerError
from flask import Blueprint, Response, g, jsonify, request, current_app, url_for
forum = Blueprint(__file__.split(".")[0], __file__.split(".")[0], url_prefix="/forums")

TIMEFRAMES: MappingProxyType = MappingProxyType({0 : lambda dt : dt - timedelta(hours=1),
                                                 1 : lambda dt : dt - timedelta(days=1),
                                                 2 : lambda dt : dt - timedelta(weeks=1),
                                                 3 : lambda dt : dt - timedelta(days=30),
                                                 4 : lambda dt : dt - timedelta(days=364),
                                                 5 : lambda _ : datetime.min})

@forum.route('/<int:forum_id>')
@pass_user_details
def get_forum(forum_id: int) -> tuple[Response, int]:
    cacheKey: str = f'forum:{forum_id}'
    fetch_relation: bool = 'fetch_relation' in request.args and g.REQUESTING_USER
    forumMapping: dict = consult_cache(RedisInterface, cacheKey, current_app.config['REDIS_TTL_CAP'], current_app.config['REDIS_TTL_PROMOTION'],current_app.config['REDIS_TTL_EPHEMERAL'])
    global_subcount, global_postcount = fetch_global_counters(RedisInterface, f'{cacheKey}:subscribers', f'{cacheKey}:posts')

    if forumMapping:
        if '__NF__' in forumMapping:
            raise NotFound('No forum with this ID exists')
        
        # Update fetch mapping with global mappings
        if global_postcount is not None:
            forumMapping['posts'] = global_postcount
        if global_subcount is not None:
            forumMapping['subscribers'] = global_subcount
    
    # Fallback to DB
    else:
        try:
            fetchedForum: Forum = db.session.execute(select(Forum)
                                                    .where(Forum.id== forum_id)
                                                    ).scalar_one_or_none()
            if not fetchedForum:
                hset_with_ttl(RedisInterface, cacheKey, {'__NF__':-1}, current_app.config['REDIS_TTL_EPHEMERAL'])
                raise NotFound('No forum with this ID could be found')
            
            forumMapping: dict = rediserialize(fetchedForum.__json_like__())

            # Update fetch mapping with global mappings
            if global_postcount is not None:
                forumMapping['posts'] = global_postcount
            if global_subcount is not None:
                forumMapping['subscribers'] = global_subcount
            # Cache mapping with updated counters
            hset_with_ttl(RedisInterface, cacheKey, forumMapping, current_app.config['REDIS_TTL_STRONG'])
        except SQLAlchemyError: genericDBFetchException()

    try:
        if fetch_relation:
            # Select forum admin/subscribed
            adminRole: str = db.session.execute(select(ForumAdmin.role)
                                                .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.REQUESTING_USER['sid']))
                                                ).scalar_one_or_none()
            isSubbed = db.session.execute(select(ForumSubscription)
                                            .where((ForumSubscription.forum_id == forum_id) & (ForumSubscription.user_id == g.REQUESTING_USER['sid']))
                                            ).scalar_one_or_none()
            forumMapping.update({'admin_role' : adminRole, 'subscribed' : bool(isSubbed)})
    except SQLAlchemyError:
        forumMapping.update({'error' : 'failed to fetch user subscriptions and admin roles in this forum'})

    return jsonify(forumMapping), 200

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
        return jsonify({'posts' : None, 'cursor' : None}), 200
    end: bool = len(nextPosts) < 6
    postsJSON: list[dict[str, Any]] = [post.__json_like__() | {'username' : username} for post, username in nextPosts]

    # Fetching each post's global counters
    global_counters: list[int] = []

    # Prepping names for counters in advance
    counter_names: list[str] = []
    for post in postsJSON:
        counter_names.append(f'post:{post["id"]}:score')
        counter_names.append(f'post:{post["id"]}:total_comments')
        counter_names.append(f'post:{post["id"]}:saves')

    global_counters = fetch_global_counters(RedisInterface, *counter_names)
    post_idx: int = 0
    for i in range(0, len(global_counters), 3): # global_counters will always have elements in multiple of 3, since a missing counter is still returned as None
        if global_counters[i] is not None:  # post score
            postsJSON[post_idx]['score'] = global_counters[i]
        if global_counters[i+1] is not None: # post comments
            postsJSON[post_idx]['comments'] = global_counters[i+1]
        if global_counters[i+2] is not None: # post saves
            postsJSON[post_idx]['saves'] = global_counters[i+2]
        post_idx+=1
        
    cursor = base64.b64encode(str(nextPosts[-1][0].id).encode('utf-8')).decode()

    return jsonify({'posts' : postsJSON, 'cursor' : cursor, 'end' : end}), 200

@forum.route("/", methods=["POST"])
@enforce_json
@token_required
def create_forum() -> tuple[Response, int]:
    try:
        userID: int = g.DECODED_TOKEN['sid']
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
@enforce_json
def delete_forum(forum_id: int) -> Response:
    cacheKey: str = f'forum:{forum_id}'
    confirmationText: str = g.REQUEST_JSON.get('confirmation')
    if not confirmationText:
        raise BadRequest('Please enter the name of the forum to follow through with deletion')
    try:
        # Check existence of this forum
        delForum: Forum = db.session.execute(select(Forum)
                                          .where((Forum.id == forum_id) & (Forum.deleted == False))
                                          ).scalar_one_or_none()
        if not delForum:
            # Broadcast non-existence
            hset_with_ttl(RedisInterface, cacheKey, {'__NF__' : -1}, current_app.config['REDIS_TTL_EPHEMERAL'])
            raise NotFound(f'No forum with id {forum_id} found. Perhaps you already deleted it?')
        
        if(delForum._name != confirmationText):
            raise BadRequest('Please enter the forum title correctly, as it is')
        
        # Check to see if user is owner or superuser
        userAdmin: ForumAdmin = db.session.execute(select(ForumAdmin)
                                                   .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.DECODED_TOKEN['sid']))
                                                   ).scalar_one_or_none()
        if not userAdmin or userAdmin.role != 'owner':
            raise Forbidden("You do not have the necessary permissions to delete this forum")

    except SQLAlchemyError: raise genericDBFetchException()
    except KeyError: raise BadRequest("Missing mandatory field 'sid', please login again")

    # Request carries the necessary permissions to delete this forum
    RedisInterface.xadd("SOFT_DELETIONS", {'id' : forum_id, 'table' : Forum.__tablename__})
    
    # Broadcast deletion
    hset_with_ttl(RedisInterface, cacheKey, {'__NF__' : -1}, current_app.config['REDIS_TTL_WEAK'])
    res = {'message' : 'forum deleted'}
    if 'redirect' in request.args:
        res['redirect'] = url_for('templates.view_anime', anime_id = delForum.anime)
    return jsonify(res), 200

@forum.route("/<int:forum_id>/admins", methods=['POST'])
@enforce_json
@token_required
def add_admin(forum_id: int) -> tuple[Response, int]:
    newAdminID: int = g.REQUEST_JSON.pop('user_id', None)
    if not newAdminID:
        raise BadRequest('Missing user id for new admin')
    if g.DECODED_TOKEN['sid'] == newAdminID:
        raise Conflict("You are already an admin in this forum")
    newAdminRole: str = g.REQUEST_JSON.pop('role', 'admin')

    newAdminLevel: int = AdminRoles.getAdminAccessLevel(newAdminRole)

    if (newAdminLevel == -1 or newAdminLevel == AdminRoles.owner) :
        raise BadRequest("Forum administrators can only have 2 roles: admin and super")
    
    
    # Request details valid at a surface level.
    try:        
        # Check to see if new admin actually exists is users table
        newAdmin: int = db.session.execute(select(User.id)
                                           .where(User.id == newAdminID)
                                           ).scalar_one_or_none()
        if not newAdmin:
            raise NotFound('No user with this user id was found')
        
        # Check if user has necessary permissions
        userAdmin: ForumAdmin = db.session.execute(select(ForumAdmin)
                                                   .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.DECODED_TOKEN['sid']))
                                                   ).scalar_one_or_none()
        if not userAdmin:
            raise Forbidden(f"You do not have the necessary permissions to add admins to: {forum._name}")
        
        if AdminRoles.getAdminAccessLevel(userAdmin.role) <= newAdminLevel:
            raise Unauthorized("You do not have the necessary permissions to add this type of admin")
        
    except SQLAlchemyError: genericDBFetchException()
    except KeyError: raise BadRequest('Mandatory claim "sid" missing in token. Please login again')

    update_global_counter(RedisInterface, f"forum:{forum_id}:admins", 1, db, Forum.__tablename__, 'admins', forum_id)   # Increment admin counter for this forum by 1
    RedisInterface.xadd('WEAK_INSERTIONS', {'table' : ForumAdmin.__tablename__, 'forum_id' : forum_id, 'user_id' : newAdminID, 'role' : newAdminRole})  # Queue forum_admins record for insertion
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
        # Check if user has necessary permissions
        userAdminRole: str = db.session.execute(select(ForumAdmin.role)
                                                .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.DECODED_TOKEN['sid']))
                                                ).scalar_one_or_none()
        requestingUserLevel: int = AdminRoles.getAdminAccessLevel(userAdminRole)
        if requestingUserLevel < 2:
            raise Forbidden('You do not have the necessary permissions to delete an admin from this forum')
        
        # Check to see if given user is actually an admin
        targetAdmin: ForumAdmin = db.session.execute(select(ForumAdmin)
                                                  .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == targetAdminID))
                                                  .with_for_update(nowait=True)
                                                  ).scalar_one_or_none()
        if not targetAdmin:
            raise NotFound("This user is not an admin")
        
        targetAdminRole = AdminRoles.getAdminAccessLevel(targetAdmin.role)
        # Must have higher access, pr same access but 
        if (requestingUserLevel < targetAdminRole or not (requestingUserLevel == targetAdminRole and targetAdminID == g.DECODED_TOKEN['sid'])):
            raise Forbidden('You do not have the necessary permissions to delete this admin from this forum')
    except SQLAlchemyError: genericDBFetchException()
    except KeyError: raise BadRequest('Mandatory claim "sid" missing in token. Please login again')

    # Add WE entry in advance
    update_global_counter(RedisInterface, f"forum:{forum_id}:admins", -1, db, Forum.__tablename__, 'admins', forum_id)   # Increment admin counter for this forum by 1
    RedisInterface.xadd('WEAK_DELETIONS', {'table' : ForumAdmin.__tablename__, 'forum_id' : forum_id, 'user_id' : targetAdminID})
    return jsonify({"message" : "Removed admin", "userID" : targetAdminID}), 202

@forum.route("/<int:forum_id>/admins", methods=['PATCH'])
@enforce_json
@token_required
def edit_admin_permissions(forum_id: int) -> tuple[Response, int]:
    # Inital check to see if admin exists
    try:
        # Check whether request is coming from owner
        forumAdmin: ForumAdmin = db.session.execute(select(ForumAdmin)
                                            .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.DECODED_TOKEN['sid']))
                                            ).scalar_one()
        
        if forumAdmin.role != 'owner':
            raise Forbidden('You must be the owner of this forum to edit admin permissions')
        
        targetID: int = g.REQUEST_JSON.pop('newAdmin', None)
        newRole: str = g.REQUEST_JSON.pop('newRole', None)

        if not targetID:
            raise BadRequest('Admin whose permission needs to be changed must be included')
        if not newRole:
            raise BadRequest('A role needs to be provided for this admin')
        if not AdminRoles.check_membership(newRole) or AdminRoles[newRole] == AdminRoles.owner:
            raise BadRequest('Invalid role, can only be super or admin')
        
        if targetID == forumAdmin.user_id:
            raise BadRequest('As owner, you cannot change your own permissions')
        
        # Check if target is admin of this forum
        targetAdmin: ForumAdmin = db.session.execute(select(ForumAdmin)
                                                     .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == targetID))
                                                     .with_for_update(nowait=True)
                                                     ).scalar_one()
    except SQLAlchemyError: genericDBFetchException()

    if not targetAdmin:
        raise NotFound('No such admin could be found')
    
    if targetAdmin.role == newRole:
        raise Conflict('This admin already has this role')
    
    # Finally, all checks passed.
    try:
        db.session.execute(update(ForumAdmin)
                            .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == targetID))
                            .values(role=newRole))
    except: raise InternalServerError('Failed to change admin role, please try again later')
    
    return jsonify({'message' : 'Admin role changed', 'admin_id' : targetID, 'new_role' : newRole}), 200

@forum.route("/<int:forum_id>/admins")
@pass_user_details
def check_admin_permissions(forum_id: int) -> tuple[Response, int]:
    if not g.REQUESTING_USER:
        return jsonify(-1), 200
    
    try:
        userRole: str = db.session.execute(select(ForumAdmin.role)
                                           .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.REQUESTING_USER.get('sid')))
                                           ).scalar_one()
        if not userRole:
            return jsonify(-1), 200
    except: return jsonify(-1), 200

    print(AdminRoles.getAdminAccessLevel(userRole))
    return jsonify(AdminRoles.getAdminAccessLevel(userRole)), 200

@forum.route("/<int:forum_id>/subscribe", methods=['PATCH'])
@token_required
def subscribe_forum(forum_id: int) -> tuple[Response, int]:
    try:
        subbedForum: ForumSubscription = db.session.execute(select(ForumSubscription)
                                                            .where((ForumSubscription.forum_id == forum_id) & (ForumSubscription.user_id == g.DECODED_TOKEN['sid']))
                                                            ).scalar_one_or_none()
        if subbedForum:
            print(1)
            return jsonify({'message' : 'Already subscribed to this forum'}), 204
        _forum = db.session.execute(select(Forum)
                                    .where(Forum.id == forum_id)
                                    ).scalar_one()

    except SQLAlchemyError: genericDBFetchException()
    except KeyError: raise BadRequest('Invalid token, please login again')

    update_global_counter(RedisInterface, f'forum:{forum_id}:subscribers', 1, db, Forum.__tablename__, 'subscribers', forum_id)
    RedisInterface.xadd("WEAK_INSERTIONS", {'user_id' : g.DECODED_TOKEN['sid'], 'forum_id' : forum_id, 'table' : ForumSubscription.__tablename__})
    return jsonify({'message' : 'subscribed!'}), 200

@forum.route("/<int:forum_id>/unsubscribe", methods=['PATCH'])
@token_required
def unsubscribe_forum(forum_id: int) -> tuple[Response, int]:
    try:
        subbedForum: ForumSubscription = db.session.execute(select(ForumSubscription)
                                                            .where((ForumSubscription.forum_id == forum_id) & (ForumSubscription.user_id == g.DECODED_TOKEN['sid']))).scalar_one_or_none()
        if not subbedForum:
            return jsonify({'message' : 'You have not subscribed to this forum'}), 204
        _forum = db.session.execute(select(Forum)
                                    .where(Forum.id == forum_id)
                                    ).scalar_one()
        
    except SQLAlchemyError: genericDBFetchException()
    except KeyError: raise BadRequest('Invalid token, please login again')

    subCounterKey: str = RedisInterface.hget(f'{Forum.__tablename__}:subscribers', forum_id)
    update_global_counter(RedisInterface, f'forum:{forum_id}:subscribers', -1, db, Forum.__tablename__, 'subscribers', forum_id)
    RedisInterface.xadd("WEAK_DELETIONS", {'user_id' : g.DECODED_TOKEN['sid'], 'forum_id' : forum_id, 'table' : ForumSubscription.__tablename__})
    return jsonify({'message' : 'unsubscribed!'}), 200

@forum.route("/<int:forum_id>", methods=["PATCH"])
@enforce_json
@token_required
def edit_forum(forum_id: int) -> tuple[Response, int]:
    # Ensure forum existence via Redis first >:3
    cacheKey: str = f'forum:{forum_id}'
    try:
        op = RedisInterface.hgetall(cacheKey)
        if '__NF__' in op:
            # Not 404, if __NF__ mapping is in cache then the forum was deleted very recently
            raise Conflict('This forum was just deleted')
        
    except RedisError: ...

    colorTheme: str | int = g.REQUEST_JSON.get('color_theme')
    if colorTheme and isinstance(colorTheme, str):
         if not colorTheme.isnumeric():
            raise BadRequest("Invalid color theme")
         colorTheme = int(colorTheme)
        
    description: str = g.REQUEST_JSON.pop('description', None)

    if not (colorTheme or description):
        raise BadRequest("No changes provided")
    
    try:
        # Ensure user has access rights for this action
        userRole: str = db.session.execute(select(ForumAdmin.role)
                                           .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.DECODED_TOKEN['sid']))
                                           ).scalar_one_or_none()

        if userRole not in ('super', 'owner'):
            raise Forbidden('You do not have access rights to edit this forum')
        
        # Lock forum
        forum: Forum = db.session.execute(select(Forum)
                                          .where(Forum.id == forum_id)
                                          .with_for_update(nowait=True)
                                          ).scalar_one()    # Forum existence is guaranteed if access control check is passed

    except SQLAlchemyError: genericDBFetchException()
    except KeyError: raise BadRequest('Invalid token, missing mandatory field: sid. Please login again')

    updateClauses: dict = {}
    if colorTheme:
        updateClauses['color_theme'] = colorTheme
    if description:
        updateClauses['description'] = description.strip()
    
    try:
        updatedForum: Forum = db.session.execute(update(Forum)
                                                 .where(Forum.id == forum_id)
                                                 .values(**updateClauses)
                                                 .returning(Forum)
                                                 ).scalar_one()
        db.session.commit()
    except SQLAlchemyError as e:
        e.__setattr__('description', 'Failed to update this forum. Please try again later')
        raise e

    hset_with_ttl(RedisInterface, cacheKey, updatedForum.__json_like__(), current_app.config['REDIS_TTL_WEAK'])
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
        userAdminRole: str = db.session.execute(select(ForumAdmin).where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.DECODED_TOKEN['sid']))).scalar_one_or_none()
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
        userAdminRole: str = db.session.execute(select(ForumAdmin).where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.DECODED_TOKEN['sid']))).scalar_one_or_none()
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
