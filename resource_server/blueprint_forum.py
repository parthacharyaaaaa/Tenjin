from flask import Blueprint, g, jsonify, request, url_for
from werkzeug import Response
from werkzeug.exceptions import BadRequest, NotFound, Forbidden, Conflict, Unauthorized, InternalServerError, Gone
from auxillary.decorators import enforce_json
from auxillary.utils import rediserialize, genericDBFetchException, consult_cache, fetch_group_resources, promote_group_ttl, cache_grouped_resource
from resource_server.models import db, Forum, User, ForumAdmin, Post, Anime, ForumSubscription, AdminRoles
from resource_server.resource_decorators import token_required, pass_user_details
from resource_server.resource_auxillary import update_global_counter, fetch_global_counters, hset_with_ttl, admin_cache_precheck, resource_cache_precheck, resource_existence_cache_precheck
from resource_server.external_extensions import RedisInterface
from resource_server.redis_config import RedisConfig
from sqlalchemy import select, update, insert, desc, Row
from sqlalchemy.sql.expression import BinaryExpression
from sqlalchemy.exc import SQLAlchemyError
from typing import Any, Sequence
from types import MappingProxyType
from datetime import datetime, timedelta
import base64
import binascii

FORUMS_BLUEPRINT: Blueprint = Blueprint(__file__.split(".")[0], __file__.split(".")[0], url_prefix="/forums")

TIMEFRAMES: MappingProxyType = MappingProxyType({0 : lambda dt : dt - timedelta(hours=1),
                                                 1 : lambda dt : dt - timedelta(days=1),
                                                 2 : lambda dt : dt - timedelta(weeks=1),
                                                 3 : lambda dt : dt - timedelta(days=30),
                                                 4 : lambda dt : dt - timedelta(days=364),
                                                 5 : lambda _ : datetime.min})

@FORUMS_BLUEPRINT.route('/<int:forum_id>')
@pass_user_details
def get_forum(forum_id: int) -> tuple[Response, int]:
    cache_key: str = f'forum:{forum_id}'
    deletion_flag_key: str = f'delete:{cache_key}'
    fetch_relation: bool = 'fetch_relation' in request.args and g.REQUESTING_USER
    deletion_intent: bool = bool(RedisInterface.get(deletion_flag_key))
    forumMapping: dict = consult_cache(RedisInterface, cache_key, RedisConfig.TTL_CAP, RedisConfig.TTL_PROMOTION, RedisConfig.TTL_EPHEMERAL)
    global_subcount, global_postcount = fetch_global_counters(RedisInterface, f'{cache_key}:subscribers', f'{cache_key}:posts')

    if deletion_intent:
        raise Gone('This forum has just been deleted')
    
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
                                                    .where((Forum.id == forum_id) & (Forum.deleted.is_(False) & (Forum.rtbf_hidden.is_(False))))
                                                    ).scalar_one_or_none()
            if not fetchedForum:
                hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
                raise NotFound('No forum with this ID could be found')
            
            forumMapping: dict = rediserialize(fetchedForum.__json_like__())

            # Update fetch mapping with global mappings
            if global_postcount is not None:
                forumMapping['posts'] = global_postcount
            if global_subcount is not None:
                forumMapping['subscribers'] = global_subcount
            # Cache mapping with updated counters
            hset_with_ttl(RedisInterface, cache_key, forumMapping, RedisConfig.TTL_STRONG)
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

@FORUMS_BLUEPRINT.route("/<int:forum_id>/posts")
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
            raise BadRequest("Failed to load more posts. Please try again later")
    
    # Confirm forum existence
    cache_key: str = f'{Forum.__tablename__}:{forum_id}'
    pagination_cache_key: str = f'{cache_key}:{Post.__tablename__}:{cursor}:{timeFrame}'
    forum_mapping: dict[str, Any] = resource_existence_cache_precheck(client=RedisInterface, identifier=forum_id, resource_name=Forum, cache_key=cache_key)

    # Even if forum is deleted, until it is persisted to DB, allow posts to be fetched
    posts, end, next_cursor = fetch_group_resources(RedisInterface, group_key=pagination_cache_key)
    counter_attrs: list[str] = ['score', 'total_comments', 'saves']
    if posts and all(posts):
        counters_mapping: dict[str, Sequence[int|None]] = fetch_global_counters(client=RedisInterface, hashmaps=[f'{Post.__tablename__}:{attr}' for attr in counter_attrs], identifiers=[post['id'] for post in posts])
        for idx, (attribute, counters) in enumerate(counters_mapping.items()):
            posts[idx][attribute] = counters[idx]
        
        # Return paginated result with updated counters
        promote_group_ttl(RedisInterface, group_key=pagination_cache_key, promotion_ttl=RedisConfig.TTL_PROMOTION, max_ttl=RedisConfig.TTL_CAP)
        return jsonify({'posts' : posts, 'cursor' : next_cursor, 'end' : end}), 200
        
    whereClause = (Post.forum_id == forum_id) & (Post.time_posted >= TIMEFRAMES[timeFrame](datetime.now()))
    if not init:
        whereClause &= (Post.id < cursor)
    
    if not forum_mapping:
        forum: Forum = db.session.execute(select(Forum)
                                          .where((Forum.id == forum_id) & (Forum.deleted.is_(False)))
                                          ).scalar_one_or_none()
        if not forum:
            hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
            raise NotFound(f'No forum with ID {forum_id} could be found')

    query = (select(Post, User.username)
             .join(User, Post.author_id == User.id)
             .where(whereClause)
             .order_by(desc(Post.score if sortOption == '1' else Post.time_posted))
             .limit(6))
    
    next_posts: list[Post] = db.session.execute(query).all()
    if not next_posts:
        return jsonify({'posts' : None, 'cursor' : None}), 200
    
    jsonified_posts: list[dict[str, Any]] = [post.__json_like__() | {'username' : username} for post, username in next_posts]
    end: bool = len(next_posts) < 6
    next_cursor: str = base64.b64encode(str(next_posts[-1][0].id).encode('utf-8')).decode()

    cache_grouped_resource(RedisInterface, group_key=pagination_cache_key,
                           resource_type=Post.__tablename__, resources={jsonified_post['id'] : rediserialize(jsonified_post) for jsonified_post in jsonified_posts},
                           weak_ttl=RedisConfig.TTL_WEAK, strong_ttl=RedisConfig.TTL_STRONG,
                           cursor=next_cursor, end=end)
    # Update newly fetched
    counters_mapping: dict[str, Sequence[int|None]] = fetch_global_counters(client=RedisInterface, hashmaps=[f'{Post.__tablename__}:{attr}' for attr in counter_attrs], identifiers=[post['id'] for post in jsonified_posts])
    for idx, (attribute, counters) in enumerate(counters_mapping.items()):
        jsonified_posts[idx][attribute] = counters[idx]

    return jsonify({'posts' : jsonified_posts, 'cursor' : next_cursor, 'end' : end}), 200

@FORUMS_BLUEPRINT.route("/", methods=["POST"])
@enforce_json
@token_required
def create_forum() -> tuple[Response, int]:
    try:
        userID: int = g.DECODED_TOKEN['sid']
        forum_name: str = str(g.REQUEST_JSON.pop('forum_name')).strip()
        if not forum_name:
            raise BadRequest('Forum creation requires a title')
        parent_anime_id: int = int(g.REQUEST_JSON.pop('anime_id'))
        description: str | None = None if 'desc' not in g.REQUEST_JSON else str(g.REQUEST_JSON.pop('desc')).strip()
    except KeyError:
        raise BadRequest("Mandatory details for forum creation missing")
    except (TypeError, ValueError):
        raise BadRequest("Malformed values provided for forum creation")
    
    # 1: Consult cache
    anime_cache_key: str = f'{Anime.__tablename__}:{parent_anime_id}'
    anime_mapping = RedisInterface.hgetall(anime_cache_key)

    if anime_mapping and RedisConfig.NF_SENTINEL_KEY in anime_mapping:
        raise NotFound(f'No anime with ID {parent_anime_id} exists')
    
    # 2: Consult DB in case of cache misses (and also to check for unique forum name)
    conflicting_forum: Forum = None
    try:
        if not anime_mapping:
            joined_result: Row = db.session.execute(select(Anime, Forum)
                                                    .outerjoin(Forum, (Forum.anime == Anime.id) & (Forum._name == forum_name) & (Forum.deleted.is_(False)))
                                                    .where(Anime.id == parent_anime_id)
                                                    ).first()
            if joined_result:
                anime_mapping: dict[str, Any] = rediserialize(joined_result[0].__json_like__())
                conflicting_forum = joined_result[1]
        else:
            conflicting_forum: Forum = db.session.execute(select(Forum)
                                                          .where((Forum._name == forum_name) & (Forum.deleted.is_(False)))
                                                          ).scalar_one_or_none()
    except SQLAlchemyError: genericDBFetchException()
    if not anime_mapping:   # Both cache and DB failed to verify this anime's existence
        hset_with_ttl(RedisInterface, anime_cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
        raise NotFound(f'No anime with ID {parent_anime_id} found')
    if conflicting_forum:
        # Non-deleted forum with same name for the same anime exists. As a last saving effort, check cache for deletion intent flag
        deletion_intent = RedisInterface.get(f'delete:{conflicting_forum.id}')
        if not deletion_intent: # No intention to delete this forum either, this request shall not pass >:(
            conflict: Conflict = Conflict(f'A forum with this name, for anime with ID {parent_anime_id} already exists')
            conflict.__setattr__('kwargs', {'forum' : conflicting_forum.__json_like__()})
            raise conflict
    
    # No duplicate forums, and parent anime actually exists
    try:
        newForum: Forum = db.session.execute(insert(Forum)
                                 .values(_name = forum_name, anime=parent_anime_id, description=description, created_at=datetime.now())
                                 .returning(Forum)).scalar_one_or_none()
        db.session.execute(insert(ForumAdmin)
                           .values(forum_id=newForum.id, user_id=userID, role='owner'))
        db.session.commit()
    except SQLAlchemyError as sqlErr:
        db.session.rollback()
        sqlErr.__setattr__("description", "An error occured while trying to create the forum. This is most likely an issue with our servers")
        raise sqlErr

    forum_cache_key: str = f'{Forum.__tablename__}:{newForum.id}'
    with RedisInterface.pipeline() as pipe:
        # Cache both newly made forum and its parent anime
        pipe.hset(forum_cache_key, mapping=rediserialize(newForum.__json_like__()))
        pipe.expire(forum_cache_key, RedisConfig.TTL_STRONG)
        pipe.hset(anime_cache_key, mapping=anime_mapping)
        pipe.expire(anime_cache_key, RedisConfig.TTL_STRONG)
        pipe.execute()

    return jsonify({"message" : "Forum created succesfully"}), 201

@FORUMS_BLUEPRINT.route("/<int:forum_id>", methods = ["DELETE"])
@token_required
@enforce_json
def delete_forum(forum_id: int) -> Response:
    cache_key: str = f'{Forum.__tablename__}:{forum_id}'
    deletion_flag_key: str = f'delete:{cache_key}'
    lock_key: str = f'lock:{deletion_flag_key}'

    with RedisInterface.pipeline() as pipe:
        pipe.hgetall(cache_key)
        pipe.get(deletion_flag_key)
        pipe.get(lock_key)
        forum_mapping, deletion_intent, lock = pipe.execute()
    
    if forum_mapping and RedisConfig.NF_SENTINEL_KEY in forum_mapping:
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Reset ephemeral announcement
        raise NotFound(f'No forum with ID {forum_id} exists')
    
    if deletion_intent:
        raise Gone(f'This forum has just been deleted')
    
    if lock:   # Some other worker is trying to delete this forum
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
            if joined_res:
                forum_mapping = joined_res[0].__json_like__()
                owner = joined_res[1]
        else:
            # Forum known, check only forum ownership
            owner = db.session.execute(select(ForumAdmin)
                                         .where(ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.DECODED_TOKEN['sid']) & (ForumAdmin.role == 'owner')
                                         ).scalar_one_or_none()
    except SQLAlchemyError: raise genericDBFetchException()
    except KeyError: raise BadRequest("Missing mandatory field 'sid', please login again")
    if not forum_mapping:
        # Broadcast non-existence
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY : RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
        raise NotFound(f'No forum with id {forum_id} found')
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
        # Write intent to deletion, append query request to soft deletions stream, and write cache_key as NF sentinel mapping for deletion
        with RedisInterface.pipeline() as pipe:
            pipe.set(deletion_flag_key, RedisConfig.RESOURCE_DELETION_PENDING_FLAG, ex=RedisConfig.TTL_STRONGEST)   # Intent value not relevant, as existence of this key alone is an indicator of deletion intent
            pipe.xadd('SOFT_DELETIONS', {'id' : forum_id, 'table' : Forum.__tablename__})
            pipe.hset(cache_key, mapping={RedisConfig.NF_SENTINEL_KEY : RedisConfig.NF_SENTINEL_VALUE})
            pipe.expire(cache_key, RedisConfig.TTL_WEAK)
            pipe.execute()
    finally:
        RedisInterface.delete(lock_key) # Free lock no matter what happens
    
    res: dict = {'message' : 'forum deleted'}
    if 'redirect' in request.args:
        res['redirect'] = url_for('templates.view_anime', anime_id = forum_mapping['anime'])
    return jsonify(res), 200

@FORUMS_BLUEPRINT.route("/<int:forum_id>/admins", methods=['POST'])
@enforce_json
@token_required
def add_admin(forum_id: int) -> tuple[Response, int]:
    admin_id: int = g.REQUEST_JSON.pop('user_id', None)
    if not admin_id:
        raise BadRequest('Missing user id for new admin')
    if g.DECODED_TOKEN['sid'] == admin_id:
        raise Conflict("You are already an admin in this forum")
    
    admin_role: str = g.REQUEST_JSON.pop('role', 'admin')
    newAdminLevel: int = AdminRoles.getAdminAccessLevel(admin_role)

    if (newAdminLevel == -1 or newAdminLevel == AdminRoles.owner):
        raise BadRequest("Forum administrators can only have 2 roles: admin and super")
    # Request details valid at a surface level

    user_cache_key: str = f'{User.__tablename__}:{admin_id}'
    forum_cache_key: str = f'{Forum.__tablename__}:{forum_id}'
    admin_flag_key: str = f'{ForumAdmin.__tablename__}:{forum_id}:{admin_id}'   # Value here would be the admin role, not a creation flag
    lock_key: str = f'lock:{admin_flag_key}'
    forum_mapping, user_mapping, _ = admin_cache_precheck(client=RedisInterface, user_id=admin_id, user_cache_key=user_cache_key, forum_id=forum_id, forum_cache_key=forum_cache_key, admin_flag=admin_flag_key, 
                                                       conflicting_intents=AdminRoles.__members__.keys(), message='This admin has already been added to this forum')
    
    try:
        if not forum_mapping:
            forum: Forum = db.session.execute(select(Forum)
                                              .where((Forum.id == forum_id) & (Forum.deleted.is_(False)))
                                              ).scalar_one_or_none()
            if not forum:
                hset_with_ttl(RedisInterface, forum_cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL, transaction=False) # Reannounce non-existence of this forum
                raise NotFound(f'No forum with ID {forum_id} found')
            forum_mapping: dict[str, Any] = forum.__json_like__()
        
        # Check if user has necessary permissions
        userAdmin: ForumAdmin = db.session.execute(select(ForumAdmin)
                                                   .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.DECODED_TOKEN['sid']))
                                                   ).scalar_one_or_none()
        if not userAdmin:
            raise Forbidden(f"You do not have the necessary permissions to add admins to: {forum_mapping['name']}")
        if AdminRoles.getAdminAccessLevel(userAdmin.role) <= newAdminLevel:
            raise Unauthorized("You do not have the necessary permissions to add this type of admin")
        
        # Consult DB in cases of partial/full cache misses
        if not user_mapping:    # Check to see if new admin actually exists is users table
            newAdmin: int = db.session.execute(select(User.id)
                                            .where(User.id == admin_id)
                                            ).scalar_one_or_none()
            if not newAdmin:
                raise NotFound('No user with this user id was found')
    except SQLAlchemyError: genericDBFetchException()

    # All checks passed, set lock
    lock_set = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock_set:
        raise Conflict('Another process is performing this same request')
    try:
        update_global_counter(interface=RedisInterface, delta=1, database=db, table=Forum.__tablename__, column='admin_count', identifier=forum_id)
        # Write intent as this admin's role and append query request to weak insertions streams
        with RedisInterface.pipeline(transaction=False) as pipe:
            pipe.set(admin_flag_key, admin_role, ex=RedisConfig.TTL_STRONGEST)
            pipe.xadd('WEAK_INSERTIONS', fields={'table' : ForumAdmin.__tablename__, 'forum_id' : forum_id, 'user_id' : admin_id, 'role' : admin_role})
            pipe.execute()
    finally:
        RedisInterface.delete(lock_key)    
    
    return jsonify({"message" : "Added new admin", "user_id" : admin_id, "role" : admin_role, "forum" : forum_mapping['name']}), 201

@FORUMS_BLUEPRINT.route("/<int:forum_id>/admins", methods=['DELETE'])
@enforce_json
@token_required
def remove_admin(forum_id: int) -> tuple[Response, int]:
    admin_id: int = g.REQUEST_JSON.pop('user_id', None)
    if not admin_id:
        raise BadRequest('Missing user id for new admin')
    
    user_cache_key: str = f'{User.__tablename__}:{admin_id}'
    forum_cache_key: str = f'{Forum.__tablename__}:{forum_id}'
    admin_flag_key: str = f'{ForumAdmin.__tablename__}:{forum_id}:{admin_id}'
    lock_key: str = f'lock:{admin_flag_key}'

    forum_mapping, user_mapping, _ = admin_cache_precheck(client=RedisInterface, user_id=admin_id, user_cache_key=user_cache_key, forum_id=forum_id, forum_cache_key=forum_cache_key, admin_flag=admin_flag_key, 
                                                       conflicting_intents=[RedisConfig.RESOURCE_DELETION_PENDING_FLAG], message='This admin has already been removed from this forum')    
    try:
        if not forum_mapping:
            forum: Forum = db.session.execute(select(Forum)
                                              .where((Forum.id == forum_id) & (Forum.deleted.is_(False)))
                                              ).scalar_one_or_none()
            if not forum:
                hset_with_ttl(RedisInterface, forum_cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL, transaction=False) # Reannounce non-existence of this forum
                raise NotFound(f'No forum with ID {forum_id} found')
            forum_mapping: dict[str, Any] = forum.__json_like__()

        # Check requesting user's permissions in this forum (This also indirectly confirms Forum existence)
        user_admin_role: str = db.session.execute(select(ForumAdmin.role)
                                                  .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.DECODED_TOKEN['sid']))
                                                  ).scalar_one_or_none()
        if not user_admin_role:
            raise Forbidden('You are not an admin in this forum, and hance cannot perform this action')
        
        # Check to see if target user is actually an admin in this forum
        forum_admin: ForumAdmin = db.session.execute(select(ForumAdmin)
                                                    .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == admin_id))
                                                    .with_for_update(nowait=True)
                                                    ).scalar_one_or_none()
        if not forum_admin:
            raise NotFound("This user is not an admin in this forum")

        if not(AdminRoles.getAdminAccessLevel(user_admin_role) > AdminRoles.getAdminAccessLevel(forum_admin.role) or 
               admin_id == g.DECODED_TOKEN['sid']):
            # Must have higher access, only other case allowed is if the admin removes themselves from their role 
            raise Forbidden(f'You do not have the necessary permissions to delete admins with role {forum_admin.role} from this forum')        
    except SQLAlchemyError: genericDBFetchException()

    # Set lock for this action
    lock_set = RedisInterface.set(lock_key, 1, RedisConfig.TTL_STRONG, nx=True)
    if not lock_set:
        raise Conflict('Another worker is already performing this action')
    try:
        update_global_counter(interface=RedisInterface, delta=-1, database=db, table=Forum.__tablename__, column='admin_count', identifier=forum_id)
        # Write intent as deletion and add deletion request to stream
        with RedisInterface.pipeline(transaction=False) as pipe:
            pipe.set(admin_flag_key, value=RedisConfig.RESOURCE_DELETION_PENDING_FLAG, ex=RedisConfig.TTL_STRONGEST)
            pipe.xadd('WEAK_DELETIONS', {'table' : ForumAdmin.__tablename__, 'forum_id' : forum_id, 'user_id' : admin_id})
            pipe.execute()

    finally:
        RedisInterface.delete(lock_key)
    
    return jsonify({"message" : "Removed admin", "user_id" : admin_id, "role" : forum_admin.role, "forum" : forum_id, "forum_name" : forum_mapping['name']}), 202

@FORUMS_BLUEPRINT.route("/<int:forum_id>/admins", methods=['PATCH'])
@enforce_json
@token_required
def edit_admin_permissions(forum_id: int) -> tuple[Response, int]:
    admin_id: int = g.REQUEST_JSON.pop('user_id', None)
    new_role: str = g.REQUEST_JSON.pop('role', '').strip()

    if not admin_id: raise BadRequest('Admin whose permission needs to be changed must be included')
    if not new_role: raise BadRequest('A role needs to be provided for this admin')
    if (not AdminRoles.check_membership(new_role)) or (AdminRoles[new_role] == AdminRoles.owner): 
        raise BadRequest('Invalid role, can only be super or admin')
    
    # Cache precheck
    user_cache_key: str = f'{User.__tablename__}:{admin_id}'
    forum_cache_key: str = f'{Forum.__tablename__}:{forum_id}'
    admin_flag_key: str = f'{ForumAdmin.__tablename__}:{forum_id}:{admin_id}'   # Value here would be the admin role, not a creation flag
    lock_key: str = f'lock:{admin_flag_key}'

    forum_mapping, _, latest_intent = admin_cache_precheck(client=RedisInterface, user_id=admin_id, user_cache_key=user_cache_key, 
                                                           forum_id=forum_id, forum_cache_key=forum_cache_key, admin_flag=admin_flag_key, 
                                                           conflicting_intents=[new_role], message=f'This admin has already has role {new_role} in this forum')        
    forum_admin_clauses: BinaryExpression = (ForumAdmin.forum_id == forum_id) & (ForumAdmin.role == 'owner') & (ForumAdmin.user_id == g.DECODED_TOKEN['sid'])
    try:
        if not forum_mapping:
            joined_result: Row= db.session.execute(select(Forum, ForumAdmin)
                                              .outerjoin(ForumAdmin, forum_admin_clauses)
                                              .where(Forum.id == forum_id)
                                              ).first()
            if joined_result:
                forum_mapping: dict[str, Any] = joined_result[0].__json_like__()
                forum_owner: ForumAdmin = joined_result[1]            
        else:
            forum_owner: ForumAdmin = db.session.execute(select(ForumAdmin)
                                                         .where(forum_admin_clauses)
                                                         ).scalar_one_or_none()
        # Exit early on forum non-existence or missing permissions
        if not forum_mapping:
            hset_with_ttl(RedisInterface, forum_cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
            raise NotFound(f'No forum with id {forum_id} exists')
        if not forum_owner: raise Forbidden('You do not have permissions to edit admin roles in this forum')

        if admin_id == forum_owner.user_id: raise BadRequest('As owner, you cannot change your own permissions')
        
        # Check if target is an admin in this forum
        forum_admin: ForumAdmin = db.session.execute(select(ForumAdmin)
                                                     .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == admin_id))
                                                     .with_for_update(nowait=True)
                                                     ).scalar_one_or_none()
        if not forum_admin:
            raise NotFound('No such admin could be found for this forum')
        if latest_intent == new_role or (latest_intent is None and forum_admin.role == new_role):
            raise Conflict(f'This admin already has the role of {new_role} in this forum')
    except SQLAlchemyError: genericDBFetchException()

    # Finally, all checks passed.
    lock_set = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock_set:
        raise Conflict('Failed to update admin permission as another request for this admin is being processed, please try again')
    
    try:
        db.session.execute(update(ForumAdmin)
                           .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == admin_id))
                           .values(role=new_role))
    except SQLAlchemyError: 
        RedisInterface.delete(lock_key)
        raise InternalServerError('Failed to change admin role, please try again later')
    
    with RedisInterface.pipeline() as pipe:
        pipe.set(admin_flag_key, value=new_role, ex=RedisConfig.TTL_STRONGEST)
        pipe.delete(lock_key)
        pipe.execute()
    
    return jsonify({'message' : 'Admin role changed', 'admin_id' : admin_id, 'new_role' : new_role, 'previous_role' :  forum_admin.role}), 200

@FORUMS_BLUEPRINT.route("/<int:forum_id>/admins")
@pass_user_details
def check_admin_permissions(forum_id: int) -> tuple[Response, int]:
    if not g.REQUESTING_USER:
        return jsonify(-1), 200
    
    try:
        role: str = db.session.execute(select(ForumAdmin.role)
                                           .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.REQUESTING_USER.get('sid')))
                                           ).scalar_one()
        if not role:
            return jsonify(-1), 200
    except: return jsonify(-1), 200

    return jsonify(AdminRoles.getAdminAccessLevel(role)), 200

@FORUMS_BLUEPRINT.route("/<int:forum_id>/subscribe", methods=['POST'])
@token_required
def subscribe_forum(forum_id: int) -> tuple[Response, int]:
    cache_key: str = f'{Forum.__tablename__}:{forum_id}'
    flag_key: str = f'{ForumSubscription.__tablename__}:{g.DECODED_TOKEN["sid"]}:{forum_id}'
    lock_key: str = f'lock:{flag_key}'

    forum_mapping, latest_intent = resource_cache_precheck(client=RedisInterface, identifier=forum_id, cache_key=cache_key, deletion_intent_flag=f'delete:{cache_key}', action_flag=flag_key, lock_name=lock_key, resource_name=Forum.__tablename__, conflicting_intent=RedisConfig.RESOURCE_CREATION_PENDING_FLAG)
    
    # In cases of partia/complete cache misses, consult DB
    prior_sub: bool = latest_intent != RedisConfig.RESOURCE_DELETION_PENDING_FLAG
    try:
        if not (forum_mapping or latest_intent):
            joined_result: Row = db.session.execute(select(Forum, ForumSubscription)
                                                    .outerjoin(ForumSubscription, (ForumSubscription.forum_id == forum_id) & (ForumSubscription.user_id == g.DECODED_TOKEN['sid']))
                                                    .where(Forum.id == forum_id)).first()
            if joined_result:
                forum_mapping: dict[str, Any] = joined_result[0].__json_like__()
                prior_sub = joined_result[1]
        elif not latest_intent:
            prior_sub = db.session.execute(select(ForumSubscription)
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
    if prior_sub:
        raise Conflict('Forum already subscribed')
    
    # Fetch forum owner (aura increase on owned forums gaining subscribers)
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
        update_global_counter(interface=RedisInterface, delta=1, database=db, table=Forum.__tablename__, column='subscribers', identifier=forum_id)
        update_global_counter(interface=RedisInterface, delta=1, database=db, table=User.__tablename__, column='aura', identifier=ownerID)
        # Write intent as creation and append weak insertion request to stream
        with RedisInterface.pipeline(transaction=False) as pipe:
            pipe.set(flag_key, RedisConfig.RESOURCE_CREATION_PENDING_FLAG, ex=RedisConfig.TTL_STRONGEST)
            pipe.xadd('WEAK_INSERTIONS', {'user_id' : g.DECODED_TOKEN['sid'], 'forum_id' : forum_id, 'table' : ForumSubscription.__tablename__})
            pipe.execute()
    finally:
        RedisInterface.delete(lock_key)
    
    return jsonify({'message' : 'Forum subscribed!'}), 202

@FORUMS_BLUEPRINT.route("/<int:forum_id>/unsubscribe", methods=['DELETE'])
@token_required
def unsubscribe_forum(forum_id: int) -> tuple[Response, int]:    
    cache_key: str = f'{Forum.__tablename__}:{forum_id}'
    flag_key: str = f'{ForumSubscription.__tablename__}:{g.DECODED_TOKEN["sid"]}:{forum_id}'
    lock_key: str = f'lock:{flag_key}'

    # Even if a forum is deleted, a user should be still allowed to unsubscribe from it
    with RedisInterface.pipeline() as pipe:
        pipe.get(f'delete:{cache_key}')
        pipe.hgetall(cache_key)
        pipe.get(flag_key)
        pipe.get(lock_key)
        deletion_intent, forum_mapping, latest_intent, lock = pipe.execute()
    
    if (forum_mapping and RedisConfig.NF_SENTINEL_KEY in forum_mapping) and not deletion_intent:  # Forum not found
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Reset ephemeral announcement
        raise NotFound(f'No forum with ID {forum_id} exists')
    
    if lock or latest_intent == RedisConfig.RESOURCE_DELETION_PENDING_FLAG: # Lock set for same request, or repeated request
        raise Conflict('A request for this action is already underway')
        
    prior_sub: bool = latest_intent == RedisConfig.RESOURCE_CREATION_PENDING_FLAG
    try:
        if not (forum_mapping or latest_intent):
            joined_result: Row = db.session.execute(select(Forum, ForumSubscription)
                                                    .outerjoin(ForumSubscription, (ForumSubscription.forum_id == forum_id) & (ForumSubscription.user_id == g.DECODED_TOKEN['sid']))
                                                    .where(Forum.id == forum_id)).first()
            if joined_result:
                forum_mapping: dict[str, Any] = joined_result[0].__json_like__()
                prior_sub = joined_result[1]
        elif not latest_intent:
            prior_sub = db.session.execute(select(ForumSubscription)
                                                   .where((ForumSubscription.user_id == g.DECODED_TOKEN['sid']) & (ForumSubscription.forum_id == forum_id))
                                                   ).scalar_one_or_none()
        elif not forum_mapping:
            forum: Forum = db.session.execute(select(Forum)
                                               .where(Forum.id == forum_id)
                                               ).scalar_one_or_none()
            if forum: forum_mapping: dict[str, Any] = forum.__json_like__()
        
        # Fetch forum owner to reduce aura
        ownerID: int = db.session.execute(select(ForumAdmin.user_id)
                                          .where((ForumAdmin.forum_id == forum_id) & (ForumAdmin.role == 'owner'))
                                          ).scalar_one_or_none()
    except SQLAlchemyError: genericDBFetchException()
    if not forum_mapping:
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Ephemeral announcement
        raise NotFound(f'No forum with ID {forum_id} exists')
    if not prior_sub:
        raise Conflict('Forum already subscribed')
    
    if not ownerID:
        raise InternalServerError('An error occured when subscribing to this forum. Please try again sometime later')

    # All checks passed, acquire lock
    lock_set = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock_set:
        raise Conflict('A request for this action is already underway')
    try:
        update_global_counter(interface=RedisInterface, delta=-1, database=db, table=Forum.__tablename__, column='subscribers', identifier=forum_id)
        update_global_counter(interface=RedisInterface, delta=-1, database=db, table=User.__tablename__, column='aura', identifier=ownerID)
        # Write intent as deletion and append weak deletion request to stream
        with RedisInterface.pipeline(transaction=False) as pipe:
            pipe.set(flag_key, RedisConfig.RESOURCE_DELETION_PENDING_FLAG, ex=RedisConfig.TTL_STRONGEST)
            pipe.xadd('WEAK_DELETIONS', {'user_id' : g.DECODED_TOKEN['sid'], 'forum_id' : forum_id, 'table' : ForumSubscription.__tablename__})
            pipe.execute()
    finally:
        RedisInterface.delete(lock_key)
    
    return jsonify({'message' : 'Forum unsubscribed!'}), 202

@FORUMS_BLUEPRINT.route("/<int:forum_id>", methods=["PATCH"])
@enforce_json
@token_required
def edit_forum(forum_id: int) -> tuple[Response, int]:
    description: str = g.REQUEST_JSON.pop('description', '').strip()
    title: str = g.REQUEST_JSON.pop('title', '').strip()
    if not (title or description):
        raise BadRequest("No changes provided")
    
    # Ensure forum existence via Redis first >:3
    cache_key: str = f'forum:{forum_id}'
    forum_mapping: dict[str, Any] = resource_existence_cache_precheck(client=RedisInterface, identifier=forum_id, resource_name=Forum.__tablename__, cache_key=cache_key, deletion_flag_key=f'delete:{cache_key}')

    admin_clause: BinaryExpression = (ForumAdmin.forum_id == forum_id) & (ForumAdmin.user_id == g.DECODED_TOKEN['sid'])
    try:
        # Even though we may have had a cache hit on forum, we still need to lock it at a DB level
        joined_result: Row = db.session.execute(select(Forum, ForumAdmin.role)
                                                .outerjoin(ForumAdmin, admin_clause)
                                                .where((Forum.id == forum_id) & (Forum.deleted.is_(False)))
                                                .with_for_update(of=Forum, nowait=True)
                                                ).first()
        if not joined_result:
            hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
            raise NotFound(f'No forum with id {forum_id} exists')

        forum_mapping: dict[str, Any] = joined_result[0].__json_like__()
        role: str = joined_result[1]
  
        if role not in ('super', 'owner'):  # Works for role being None too
            raise Forbidden('You do not have access rights to edit this forum')
    except SQLAlchemyError: genericDBFetchException()

    updateClauses: dict[str, str] = {}
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
    hset_with_ttl(RedisInterface, cache_key, rediserialize(forum_mapping), RedisConfig.TTL_WEAK)
    return jsonify({'message' : 'Forum edited succesfully', 'forum' : forum_mapping}), 200

@FORUMS_BLUEPRINT.route("/<int:forum_id>/highlight-post", methods=['PATCH'])
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

@FORUMS_BLUEPRINT.route("/<int:forum_id>/highlight-post", methods=['DELETE'])
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
