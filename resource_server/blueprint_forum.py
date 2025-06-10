from flask import Blueprint, g, jsonify, request, url_for
from werkzeug import Response
from werkzeug.exceptions import BadRequest, NotFound, Forbidden, Conflict, Unauthorized, InternalServerError
from auxillary.decorators import enforce_json
from auxillary.utils import rediserialize, genericDBFetchException, consult_cache
from resource_server.models import db, Forum, User, ForumAdmin, Post, Anime, ForumSubscription, AdminRoles
from resource_server.resource_decorators import token_required, pass_user_details
from resource_server.resource_auxillary import update_global_counter, fetch_global_counters
from resource_server.external_extensions import RedisInterface, hset_with_ttl
from resource_server.redis_config import RedisConfig
from sqlalchemy import select, update, insert, desc, Row
from sqlalchemy.exc import SQLAlchemyError
from typing import Any
from types import MappingProxyType
from datetime import datetime, timedelta
from redis.exceptions import RedisError
import base64
import binascii

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
    forumMapping: dict = consult_cache(RedisInterface, cacheKey, RedisConfig.TTL_CAP, RedisConfig.TTL_PROMOTION, RedisConfig.TTL_EPHEMERAL)
    global_subcount, global_postcount = fetch_global_counters(RedisInterface, f'{cacheKey}:subscribers', f'{cacheKey}:posts')

    if forumMapping:
        if RedisConfig.NF_SENTINEL_KEY in forumMapping:
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
                hset_with_ttl(RedisInterface, cacheKey, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
                raise NotFound('No forum with this ID could be found')
            
            forumMapping: dict = rediserialize(fetchedForum.__json_like__())

            # Update fetch mapping with global mappings
            if global_postcount is not None:
                forumMapping['posts'] = global_postcount
            if global_subcount is not None:
                forumMapping['subscribers'] = global_subcount
            # Cache mapping with updated counters
            hset_with_ttl(RedisInterface, cacheKey, forumMapping, RedisConfig.TTL_STRONG)
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
            hset_with_ttl(RedisInterface, f'anime:{forumAnimeID}', {RedisConfig.NF_SENTINEL_KEY : RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
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

    hset_with_ttl(RedisInterface, cacheKey, rediserialize(newForum.__json_like__()), RedisConfig.TTL_STRONG)
    return jsonify({"message" : "Forum created succesfully"}), 202

@forum.route("/<int:forum_id>", methods = ["DELETE"])
@token_required
@enforce_json
def delete_forum(forum_id: int) -> Response:
    cache_key: str = f'{Forum.__tablename__}:{forum_id}'
    flag_key: str = f'delete:{cache_key}'
    lock_key: str = f'lock:{flag_key}'

    with RedisInterface.pipeline() as pipe:
        pipe.hgetall(cache_key)
        pipe.get(flag_key)
        pipe.get(lock_key)
        forum_mapping, latest_intent, lock = pipe.execute()
    
    if forum_mapping and RedisConfig.NF_SENTINEL_KEY in forum_mapping:
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Reset ephemeral announcement
        raise NotFound(f'No forum with ID {forum_id} exists')
    if lock or latest_intent:   # Either some other worker is trying to delete this forum, or this forum has already been queued for deletion but not flushed to database yet
        raise Conflict(f'A request for this action is currently enqueued')
    
    confirmationText: str = g.REQUEST_JSON.get('confirmation')
    if not confirmationText:
        raise BadRequest('Please enter the name of the forum to follow through with deletion')
    
    # Validating request
    try:
        if not forum_mapping:
            # Check existence of this forum AND owner
            joined_res: Row = db.session.execute(select(Forum, ForumAdmin)
                                                 .outerjoin(ForumAdmin, (ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.DECODED_TOKEN['sid']) & (ForumAdmin.role == 'owner'))
                                                 .where((Forum.id == forum_id) & (Forum.deleted.is_(False)))
                                                 ).first()
            if not joined_res:
                # Broadcast non-existence
                hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY : RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
                raise NotFound(f'No forum with id {forum_id} found')
            forum_mapping = joined_res[0].__json_like__()
            owner = joined_res[1]
        else:
            # Forum known, check only admins
            owner = db.session.execute(select(ForumAdmin)
                                         .where(ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.DECODED_TOKEN['sid']) & (ForumAdmin.role == 'owner')
                                         ).scalar_one_or_none()
    except SQLAlchemyError: raise genericDBFetchException()        
    except KeyError: raise BadRequest("Missing mandatory field 'sid', please login again")
    if not owner:
        raise Forbidden("You do not have the necessary permissions to delete this forum")
    if(forum_mapping['name'] != confirmationText):
        raise BadRequest('Please enter the forum title correctly, as it is')

    # Permission valid, and all other checks passed. Attempt to set lock for this action
    lock = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock:
        # Failed to acquire lock means another worker is performing this same request, treat this request as a duplicate
        raise Conflict(f'A request for this action is currently enqueued')
    try:
        RedisInterface.set(flag_key, RedisConfig.RESOURCE_DELETION_PENDING_FLAG, ex=RedisConfig.TTL_STRONGEST)
        RedisInterface.xadd("SOFT_DELETIONS", {'id' : forum_id, 'table' : Forum.__tablename__})
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY : RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_WEAK)   # Broadcast deletion
    finally:
        RedisInterface.delete(lock_key) # Free lock no matter what happens
    
    res: dict = {'message' : 'forum deleted'}
    if 'redirect' in request.args:
        res['redirect'] = url_for('templates.view_anime', anime_id = forum_mapping['anime'])
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

    if (newAdminLevel == -1 or newAdminLevel == AdminRoles.owner):
        raise BadRequest("Forum administrators can only have 2 roles: admin and super")
    # Request details valid at a surface level

    user_cache_key: str = f'{User.__tablename__}:{newAdminID}'
    cache_key: str = f'{Forum.__tablename__}:{forum_id}'
    flag_key: str = f'{ForumAdmin.__tablename__}:{forum_id}:{newAdminID}'   # Value here would be the admin role, not a creation flag
    lock_key: str = f'lock:{flag_key}'
    with RedisInterface.pipeline() as pipe:
        pipe.hgetall(user_cache_key)
        pipe.hgetall(cache_key)
        pipe.get(flag_key)
        pipe.get(lock_key)
        user_mapping, forum_mapping, latest_intent, lock = pipe.execute()
    
    if forum_mapping and RedisConfig.NF_SENTINEL_KEY in forum_mapping:  # Forum doesn't exist
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL, transaction=False) # Reannounce non-existence of this forum
        raise NotFound(f'No forum with ID {forum_id} found')
    if user_mapping and RedisConfig.NF_SENTINEL_KEY in user_mapping:    # User (to be made an admin) doesn't exist
        hset_with_ttl(RedisInterface, user_cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL, transaction=False) # Reannounce non-existence of this forum
        raise NotFound(f'No forum with ID {forum_id} found')
    if latest_intent in ('admin', 'super') or lock:
        raise Conflict('A request for this action is already enqueued')
    
    try:
        # Check if user has necessary permissions
        userAdmin: ForumAdmin = db.session.execute(select(ForumAdmin)
                                                   .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.DECODED_TOKEN['sid']))
                                                   ).scalar_one_or_none()
        if not userAdmin:
            raise Forbidden(f"You do not have the necessary permissions to add admins to: {forum._name}")
        if AdminRoles.getAdminAccessLevel(userAdmin.role) <= newAdminLevel:
            raise Unauthorized("You do not have the necessary permissions to add this type of admin")
        
        # Consult DB in cases of partial/full cache misses
        if not user_mapping:    # Check to see if new admin actually exists is users table
            newAdmin: int = db.session.execute(select(User.id)
                                            .where(User.id == newAdminID)
                                            ).scalar_one_or_none()
            if not newAdmin:
                raise NotFound('No user with this user id was found')
    except SQLAlchemyError: genericDBFetchException()
    except KeyError: raise BadRequest('Mandatory claim "sid" missing in token. Please login again')

    # All checks passed, set lock
    lock_set = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock_set:
        raise Conflict('Another process is performing this same request')
    try:
        RedisInterface.set(flag_key, value=newAdminRole, ex=RedisConfig.TTL_STRONGEST)  # Write intent as admin creation to newAdminRole
        update_global_counter(interface=RedisInterface, delta=1, database=db, table=Forum.__tablename__, column='admin_count', identifier=forum_id)
        RedisInterface.xadd('WEAK_INSERTIONS', {'table' : ForumAdmin.__tablename__, 'forum_id' : forum_id, 'user_id' : newAdminID, 'role' : newAdminRole})  # Queue forum_admins record for insertion
    finally:
        RedisInterface.delete(lock_key)    
    
    return jsonify({"message" : "Added new admin", "userID" : newAdminID, "role" : newAdminRole}), 202

@forum.route("/<int:forum_id>/admins", methods=['DELETE'])
@enforce_json
@token_required
def remove_admin(forum_id: int) -> tuple[Response, int]:
    target_admin_id: int = g.REQUEST_JSON.pop('user_id', None)
    if not target_admin_id:
        raise BadRequest('Missing user id for new admin')
    
    user_cache_key: str = f'{User.__tablename__}:{target_admin_id}'
    cache_key: str = f'{Forum.__tablename__}:{forum_id}'
    flag_key: str = f'{ForumAdmin.__tablename__}:{forum_id}:{target_admin_id}'
    lock_key: str = f'lock:{flag_key}'
    with RedisInterface.pipeline() as pipe:
        pipe.hgetall(user_cache_key)
        pipe.hgetall(cache_key)
        pipe.get(flag_key)
        pipe.get(lock_key)
        user_mapping, forum_mapping, latest_intent, lock = pipe.execute()
    
    if forum_mapping and RedisConfig.NF_SENTINEL_KEY in forum_mapping:  # Forum doesn't exist
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL, transaction=False) # Reannounce non-existence of this forum
        raise NotFound(f'No forum with ID {forum_id} found')
    if user_mapping and RedisConfig.NF_SENTINEL_KEY in user_mapping:  # Forum doesn't exist
        hset_with_ttl(RedisInterface, user_cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL, transaction=False) # Reannounce non-existence of this forum
        raise NotFound(f'No user with ID {target_admin_id} found')
    if latest_intent == RedisConfig.RESOURCE_DELETION_PENDING_FLAG or lock:
        raise Conflict('Another worker is processing this request')
    
    try:        
        # Check requesting user's permissions in this forum (This also confirms the existence of this forum)
        user_admin_role: str = db.session.execute(select(ForumAdmin.role)
                                                  .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.DECODED_TOKEN['sid']))
                                                  ).scalar_one_or_none()
        requesting_user_level: int = AdminRoles.getAdminAccessLevel(user_admin_role)
        if requesting_user_level < 2:
            raise Forbidden('You do not have the necessary permissions to delete an admin from this forum')
        
        if not latest_intent:
            # Check to see if target user is actually an admin in this forum, if not found from cache
            targetAdmin: ForumAdmin = db.session.execute(select(ForumAdmin)
                                                        .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == target_admin_id))
                                                        .with_for_update(nowait=True)
                                                        ).scalar_one_or_none()
            if not targetAdmin:
                raise NotFound("This user is not an admin in this forum")
            target_admin_level: int = AdminRoles.getAdminAccessLevel(targetAdmin.role)
        else:
            target_admin_level: int = AdminRoles.getAdminAccessLevel(latest_intent)

        # Must have higher access, only other case allowed is if the admin removes themselves from their role 
        if (requesting_user_level < target_admin_level or not (requesting_user_level == target_admin_level and target_admin_id == g.DECODED_TOKEN['sid'])):
            raise Forbidden('You do not have the necessary permissions to delete this admin from this forum')
    except SQLAlchemyError: genericDBFetchException()
    except KeyError: raise BadRequest('Mandatory claim "sid" missing in token. Please login again')

    # Set lock for this action
    lock_set = RedisInterface.set(lock_key, 1, RedisConfig.TTL_STRONG, nx=True)
    if not lock_set:
        raise Conflict('Another worker is already performing this action')
    try:
        RedisInterface.set(flag_key, value=RedisConfig.RESOURCE_DELETION_PENDING_FLAG, ex=RedisConfig.TTL_STRONGEST)    # Write latest intent as deletion for this admin
        # Decrement global admin count and delete forum_admins record
        update_global_counter(interface=RedisInterface, delta=-1, database=db, table=Forum.__tablename__, column='admin_count', identifier=forum_id)
        RedisInterface.xadd('WEAK_DELETIONS', {'table' : ForumAdmin.__tablename__, 'forum_id' : forum_id, 'user_id' : target_admin_id})
    finally:
        RedisInterface.delete(lock_key)
    
    return jsonify({"message" : "Removed admin", "userID" : target_admin_id, "role" : latest_intent or targetAdmin.role}), 202

@forum.route("/<int:forum_id>/admins", methods=['PATCH'])
@enforce_json
@token_required
def edit_admin_permissions(forum_id: int) -> tuple[Response, int]:
    targetID: int = g.REQUEST_JSON.pop('newAdmin', None)
    newRole: str = g.REQUEST_JSON.pop('newRole', '').strip()

    if not targetID:
        raise BadRequest('Admin whose permission needs to be changed must be included')
    if not newRole:
        raise BadRequest('A role needs to be provided for this admin')
    if not AdminRoles.check_membership(newRole) or AdminRoles[newRole] == AdminRoles.owner:
        raise BadRequest('Invalid role, can only be super or admin')
    
    flag_key: str = f'{ForumAdmin.__tablename__}:{forum_id}:{targetID}'
    latest_intent: str = RedisInterface.get(flag_key)
    if latest_intent == RedisConfig.RESOURCE_DELETION_PENDING_FLAG:
        raise NotFound(f'No admin (ID: {targetID}) found for this forum (ID: {forum_id})')
    
    try:
        # Check whether request is coming from owner
        forumAdmin: ForumAdmin = db.session.execute(select(ForumAdmin)
                                                    .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.DECODED_TOKEN['sid']) & (ForumAdmin.role == 'owner'))
                                                    ).scalar_one()
        
        if not forumAdmin:
            raise Forbidden('You must be the owner of this forum to edit admin permissions')
        
        if targetID == forumAdmin.user_id:
            raise BadRequest('As owner, you cannot change your own permissions')
        
        if not latest_intent:
            # Check if target is admin of this forum
            targetAdmin: ForumAdmin = db.session.execute(select(ForumAdmin)
                                                        .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == targetID))
                                                        .with_for_update(nowait=True)
                                                        ).scalar_one()
            if not targetAdmin:
                raise NotFound('No such admin could be found for this forum')
            latest_intent: str = targetAdmin.role
        
    except SQLAlchemyError: genericDBFetchException()
    if latest_intent == newRole:
        raise Conflict('This admin already has this role')

    # Finally, all checks passed.
    lock_key: str = f'lock:{flag_key}'
    lock_set = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock_set:
        raise Conflict('Failed to update admin permission as another request for this admin is being processed, please try again')
    
    with RedisInterface.pipeline() as pipe:
        pipe.set(flag_key, value=newRole, ex=RedisConfig.TTL_STRONGEST)
        pipe.delete(lock_key)
        pipe.execute()
    try:
        db.session.execute(update(ForumAdmin)
                           .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == targetID))
                           .values(role=newRole))
    except SQLAlchemyError: raise InternalServerError('Failed to change admin role, please try again later')
    
    return jsonify({'message' : 'Admin role changed', 'admin_id' : targetID, 'new_role' : newRole, 'previous_role' : latest_intent or targetAdmin.role}), 200

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
    cache_key: str = f'{Forum.__tablename__}:{forum_id}'
    flag_key: str = f'{ForumSubscription.__tablename__}:{g.DECODED_TOKEN["sid"]}:{forum_id}'
    lock_key: str = f'lock:{flag_key}'

    with RedisInterface.pipeline() as pipe:
        pipe.hgetall(cache_key)
        pipe.get(flag_key)
        pipe.get(lock_key)
        forum_mapping, latest_intent, lock = pipe.execute()
     
    # Check for 404 sentinal value
    if forum_mapping and RedisConfig.NF_SENTINEL_KEY in forum_mapping:
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Reset ephemeral announcement
        raise NotFound(f'No forum with ID {forum_id} exists')
    if lock or latest_intent == RedisConfig.RESOURCE_CREATION_PENDING_FLAG: # Lock set for same request, or repeated request
        raise Conflict('A request for this action is already underway')
    
    # In cases of partia/complete cache misses, consult DB
    priorSubscription: bool = latest_intent != RedisConfig.RESOURCE_DELETION_PENDING_FLAG
    try:
        if not (forum_mapping or latest_intent):
            joined_result: Row = db.session.execute(select(Forum, ForumSubscription)
                                                    .outerjoin(ForumSubscription, (ForumSubscription.forum_id == forum_id) & (ForumSubscription.user_id == g.DECODED_TOKEN['sid']))
                                                    .where(Forum.id == forum_id)).first()
            if joined_result:
                forum_mapping: dict[str, Any] = joined_result[0].__json_like__()
                priorSubscription = joined_result[1]
        elif not latest_intent:
            priorSubscription = db.session.execute(select(ForumSubscription)
                                                   .where((ForumSubscription.user_id == g.DECODED_TOKEN['sid']) & (ForumSubscription.forum_id == forum_id))
                                                   ).scalar_one_or_none()
        elif not forum_mapping:
            forum: Forum = db.session.execute(select(Forum)
                                              .where(Forum.id == forum_id)
                                              ).scalar_one_or_none()
            if forum:
                forum_mapping: dict[str, Any] = forum.__json_like__()
    except SQLAlchemyError: genericDBFetchException()
    if not forum_mapping:
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Ephemeral announcement
        raise NotFound(f'No forum with ID {forum_id} exists')
    if priorSubscription:
        raise Conflict('Forum already subscribed')
    
    # Fetch forum owner
    ownerID: int = db.session.execute(select(ForumAdmin.user_id)
                                      .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.role == 'owner'))
                                      ).scalar_one_or_none()
    if not ownerID:
        raise InternalServerError('An error occured when subscribing to this forum. Please try again sometime later')

    # All checks passed, acquire lock
    lock_set = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock_set:
        raise Conflict('A request for this action is already underway')
    try:
        RedisInterface.set(flag_key, value=RedisConfig.RESOURCE_CREATION_PENDING_FLAG, ex=RedisConfig.TTL_STRONGEST)
        update_global_counter(interface=RedisInterface, delta=1, database=db, table=Forum.__tablename__, column='subscribers', identifier=forum_id)
        update_global_counter(interface=RedisInterface, delta=1, database=db, table=User.__tablename__, column='aura', identifier=ownerID)
        RedisInterface.xadd("WEAK_INSERTIONS", {'user_id' : g.DECODED_TOKEN['sid'], 'forum_id' : forum_id, 'table' : ForumSubscription.__tablename__})
    finally:
        RedisInterface.delete(lock_key)
    
    return jsonify({'message' : 'Forum subscribed!'}), 202

@forum.route("/<int:forum_id>/unsubscribe", methods=['PATCH'])
@token_required
def unsubscribe_forum(forum_id: int) -> tuple[Response, int]:    
    cache_key: str = f'{Forum.__tablename__}:{forum_id}'
    flag_key: str = f'{ForumSubscription.__tablename__}:{g.DECODED_TOKEN["sid"]}:{forum_id}'
    lock_key: str = f'lock:{flag_key}'

    with RedisInterface.pipeline() as pipe:
        pipe.hgetall(cache_key)
        pipe.get(flag_key)
        pipe.get(lock_key)
        forum_mapping, latest_intent, lock = pipe.execute()
     
    if forum_mapping and RedisConfig.NF_SENTINEL_KEY in forum_mapping:
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Reset ephemeral announcement
        raise NotFound(f'No forum with ID {forum_id} exists')
    if lock or latest_intent == RedisConfig.RESOURCE_DELETION_PENDING_FLAG: # Lock set for same request, or repeated request
        raise Conflict('A request for this action is already underway')
        
    forum_exists: bool = bool(forum_mapping)
    priorSubscription: bool = latest_intent == RedisConfig.RESOURCE_CREATION_PENDING_FLAG
    try:
        if not (forum_mapping or latest_intent):
            joined_result: Row = db.session.execute(select(Forum, ForumSubscription)
                                                    .outerjoin(ForumSubscription, (ForumSubscription.forum_id == forum_id) & (ForumSubscription.user_id == g.DECODED_TOKEN['sid']))
                                                    .where(Forum.id == forum_id)).first()
            if joined_result:
                forum_exists, priorSubscription = joined_result
        elif not latest_intent:
            priorSubscription = db.session.execute(select(ForumSubscription)
                                                   .where((ForumSubscription.user_id == g.DECODED_TOKEN['sid']) & (ForumSubscription.forum_id == forum_id))
                                                   ).scalar_one_or_none()
        elif not forum_mapping:
            forum_exists = db.session.execute(select(Forum)
                                              .where(Forum.id == forum_id)
                                              ).scalar_one_or_none()
    except SQLAlchemyError: genericDBFetchException()
    if not forum_exists:
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Ephemeral announcement
        raise NotFound(f'No forum with ID {forum_id} exists')
    if not priorSubscription:
        raise Conflict('Forum already subscribed')
    
    # Fetch forum owner
    ownerID: int = db.session.execute(select(ForumAdmin.user_id)
                                      .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.role == 'owner'))
                                      ).scalar_one_or_none()
    if not ownerID:
        raise InternalServerError('An error occured when subscribing to this forum. Please try again sometime later')

    # All checks passed, acquire lock
    lock_set = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock_set:
        raise Conflict('A request for this action is already underway')
    try:
        RedisInterface.set(flag_key, value=RedisConfig.RESOURCE_DELETION_PENDING_FLAG, ex=RedisConfig.TTL_STRONGEST)
        update_global_counter(interface=RedisInterface, delta=-1, database=db, table=Forum.__tablename__, column='subscribers', identifier=forum_id)
        update_global_counter(interface=RedisInterface, delta=-1, database=db, table=User.__tablename__, column='aura', identifier=ownerID)
        RedisInterface.xadd("WEAK_DELETIONS", {'user_id' : g.DECODED_TOKEN['sid'], 'forum_id' : forum_id, 'table' : ForumSubscription.__tablename__})
    finally:
        RedisInterface.delete(lock_key)
    
    return jsonify({'message' : 'Forum unsibscribed!'}), 202

@forum.route("/<int:forum_id>", methods=["PATCH"])
@enforce_json
@token_required
def edit_forum(forum_id: int) -> tuple[Response, int]:
    # Ensure forum existence via Redis first >:3
    cacheKey: str = f'forum:{forum_id}'
    try:
        op = RedisInterface.hgetall(cacheKey)
        if RedisConfig.NF_SENTINEL_KEY in op:
            raise NotFound(f'No forum with ID {forum_id} found')
    except RedisError: ...
        
    description: str = g.REQUEST_JSON.pop('description', '').strip()
    title: str = g.REQUEST_JSON.pop('title', '').strip()
    if not (title or description):
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
    if title:
        updateClauses['title'] = title
    if description:
        updateClauses['description'] = description
    
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

    forum_mapping: dict[str, str|int] = updatedForum.__json_like__()
    hset_with_ttl(RedisInterface, cacheKey, forum_mapping, RedisConfig.TTL_WEAK)
    return jsonify({'message' : 'Forum edited succesfully', 'forum' : forum_mapping}), 200

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
        user_admin_role: str = db.session.execute(select(ForumAdmin).where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.DECODED_TOKEN['sid']))).scalar_one_or_none()
        if not user_admin_role or user_admin_role not in ('super', 'owner'):
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
        user_admin_role: str = db.session.execute(select(ForumAdmin).where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.DECODED_TOKEN['sid']))).scalar_one_or_none()
        if not user_admin_role or user_admin_role not in ('super', 'owner'):
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
