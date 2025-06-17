'''Blueprint module for all actions related to resource: users'''
from flask import Blueprint, g, request, jsonify, current_app, url_for
from werkzeug import Response
from werkzeug.exceptions import BadRequest, Conflict, InternalServerError, NotFound, Unauthorized, Forbidden, Gone
from auxillary.decorators import enforce_json
from auxillary.utils import hash_password, verify_password, genericDBFetchException, rediserialize, consult_cache, fetch_group_resources, promote_group_ttl, cache_grouped_resource
from resource_server.resource_auxillary import processUserInfo, fetch_global_counters, hset_with_ttl, resource_existence_cache_precheck
from resource_server.external_extensions import RedisInterface
from resource_server.resource_decorators import token_required
from resource_server.models import db, User, PasswordRecoveryToken, Post, Forum, ForumSubscription, Anime, AnimeSubscription
from resource_server.scripts.mail import enqueueEmail
from resource_server.redis_config import RedisConfig
from sqlalchemy import select, insert, update, delete
from sqlalchemy.sql.expression import BinaryExpression
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from datetime import datetime
from redis.exceptions import RedisError
from uuid import uuid4
import base64
from hashlib import sha256
from typing import Sequence, Any

USERS_BLUEPRINT: Blueprint = Blueprint(__file__.split(".")[0], __file__.split(".")[0], url_prefix="/users")

@USERS_BLUEPRINT.route("/", methods=["POST"])
# @private
@enforce_json
def register() -> tuple[Response, int]:
    if not (g.REQUEST_JSON.get('username') and
            g.REQUEST_JSON.get('password') and
            g.REQUEST_JSON.get('email')):
        raise BadRequest("Response requires username, password, and email")
    op, USER_DETAILS = processUserInfo(username=g.REQUEST_JSON["username"],
                                       email=g.REQUEST_JSON["email"],
                                       password=g.REQUEST_JSON["password"])
    if not op:
        raise BadRequest(USER_DETAILS.get("error"))

    response_kwargs: dict[str, str] = {}

    try:
        existingUsers: list[User] | User = db.session.execute(select(User)
                                           .where((User.username == USER_DETAILS["username"]) | (User.email == USER_DETAILS["email"]))
                                           ).scalars().all()
    except:
        raise InternalServerError()
    if existingUsers:
        current_date = datetime.now()
        # Check if dead account, if dead account is within recovery period, then raise conflict too
        if (
            not (existingUsers[0].deleted and existingUsers[-1].deleted) or
            existingUsers[0].time_deleted + current_app['ACCOUNT_RECOVERY_PERIOD'] >= current_date or
            existingUsers[-1].time_deleted + current_app['ACCOUNT_RECOVERY_PERIOD'] >= current_date
            ):
                    conflict = Conflict("An account with this username or email address already exists")
                    conflict.__setattr__("kwargs", response_kwargs)
                    raise Conflict
        
        # Hard delete both accounts that violate unique contraint for email and username. Purge time >:3
        db.session.execute(delete(User)
                           .where(User.id.in_([existingUsers[0].id, existingUsers[-1].id])))

    # All checks passed, user creation good to go
    passwordHash, passwordSalt = hash_password(USER_DETAILS.pop("password"))
    try:
        uID: int = db.session.execute(insert(User).values(email=USER_DETAILS["email"],
                                                          username=USER_DETAILS["username"],
                                                          pw_hash=passwordHash,
                                                          pw_salt=passwordSalt)
                                                          .returning(User.id)
                                                          ).scalar_one_or_none()
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        conflict = Conflict("An account with this username/email was made at the same time as your request. Tough luck :(")
        conflict.__setattr__("kwargs", response_kwargs)
        raise conflict
    except:
        db.session.rollback()
        raise InternalServerError("An error occured with our database service")
    
    # Respond to auth server
    return jsonify({"message" : "Account created",
                    "sub" : USER_DETAILS["username"],
                    "sid" : uID,
                    "email" : USER_DETAILS["email"], 
                    **response_kwargs}), 201
 
@USERS_BLUEPRINT.route("/", methods=["DELETE"])
@enforce_json
def delete_user() -> tuple[Response, int]:
    if not (g.REQUEST_JSON.get("username") and
            g.REQUEST_JSON.get("password")):
        raise BadRequest("Requires username and password to be provided")
    
    OP, USER_DETAILS = processUserInfo(username=g.REQUEST_JSON['username'], password=g.REQUEST_JSON['password'])
    if not OP:
        raise BadRequest(USER_DETAILS.get("error"))
    targetUser: User = db.session.execute(select(User)
                                          .where((User.username == USER_DETAILS['username']) & (User.deleted.is_(False)))
                                          ).scalar_one_or_none()
    if not targetUser:
        raise NotFound("Requested user could not be found")
    
    if not verify_password(g.REQUEST_JSON['password'], targetUser.pw_hash, targetUser.pw_salt):
        raise Unauthorized('Invalid credentials')
    
    cache_key: str = f'{User.__tablename__}:{targetUser.id}'
    flag_key: str = f'alive_status:{cache_key}'
    lock_key: str = f'lock:{flag_key}'
    with RedisInterface.pipeline() as pipe:
        pipe.hgetall(cache_key)
        pipe.get(flag_key)
        pipe.get(lock_key)
        user_mapping, latest_intent, lock = pipe.execute()
    
    if user_mapping and RedisConfig.NF_SENTINEL_KEY in user_mapping:
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
        raise NotFound('No user with this ID found. Perhaps you already deleted this account?')
    if lock or latest_intent == RedisConfig.RESOURCE_DELETION_PENDING_FLAG:
        raise Conflict("Another request for this account's deletion is already underway")
    
    # All checks passed, set lock
    lock_set = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock_set:
        raise Conflict('Failed to perform this action, another request for the same action is being processed')
    
    try:
        # Write intent as account recovery (represented by the usual creation flag), add user ID to account recovery stream, and write user mapping to cache
        with RedisInterface.pipeline(transaction=False) as pipe:
            pipe.set(flag_key, RedisConfig.RESOURCE_DELETION_PENDING_FLAG, ex=RedisConfig.TTL_EPHEMERAL)
            pipe.xadd('USER_ACTIVITY_DELETIONS', {'user_id' : targetUser.id, 'rtbf' : int(targetUser.rtbf), 'table' : User.__tablename__})
            pipe.hset(cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE})
            pipe.expire(cache_key, RedisConfig.TTL_WEAK)
            pipe.execute()
        enqueueEmail(RedisInterface, email=targetUser.email, subject='deletion', username=targetUser.username)
    except RedisError: 
        raise InternalServerError("Failed to perform account deletion, please try again. If the issue persists, please raise a ticket")
    finally:
        RedisInterface.delete(lock_key)
    
    return jsonify({"message" : "account deleted succesfully", "username" : targetUser.username, "time_deleted" : targetUser.time_deleted}), 203

@USERS_BLUEPRINT.route("/recover", methods=["PATCH"])
@enforce_json
def recover_user() -> Response:
    if not (g.REQUEST_JSON.get("identity") and g.REQUEST_JSON.get("password")):
        raise BadRequest("Required identity (email/username) and account password")
    
    isEmail = '@' in g.REQUEST_JSON['identity']
    OP, USER_DETAILS = processUserInfo(password=g.REQUEST_JSON['password'],
                                          email=g.REQUEST_JSON['identity'] if isEmail else None,
                                          username=g.REQUEST_JSON['identity'] if not isEmail else None)
    if not OP:
        raise BadRequest(USER_DETAILS.get('error', "Invalid user details"))
    
    # Fetch account details from DB (ID not known yet). NOTE: Clause 'User.deleted.is_(False)' is not included because DB might not be consistent yet
    deadAccount = db.session.execute(select(User)
                                     .where((User.email == USER_DETAILS.get('email') if isEmail else User.username == USER_DETAILS['username']))
                                     .with_for_update()
                                     ).scalar_one_or_none()

    if not deadAccount:
        # Never existed
        nf = NotFound(f"No deleted account with this {'email' if isEmail else 'username'} exists.")
        nf.__setattr__("kwargs", {"info" : f"If you had believe that this account is still in the recovery period of {current_app['ACCOUNT_RECOVERY_PERIOD']} days, contact support",
                                  "_links" : {"tickets" : {"href" : url_for("tickets")}}})
        raise nf
    
    if (deadAccount.time_deleted or datetime(2000,1,1,0,0,0,0)) + current_app.config['ACCOUNT_RECOVERY_PERIOD'] <= datetime.now():
        # Tough luck, its already past recovery period >:(
        cnf = Conflict("Account recovery period for this account has already expired, and its deletion is due soon. Please create a new account")
        raise cnf
    
    # Match password hashes
    if not verify_password(USER_DETAILS['password'], deadAccount.pw_hash, deadAccount.pw_salt):
        unauth = Unauthorized("Incorrect password")
        unauth.__setattr__("kwargs", {"info" : "If you have forgotten your password and want to recover your account, please perform the deleted account password recovery phase", 
                                      "_links" : {"recovery" : {"href" : url_for(".recovery")}}})
        raise unauth
    
    user_mapping: dict[str, str|int] = deadAccount.__json_like__()
    # Check cache for state
    cache_key: str = f'{User.__tablename__}:{deadAccount.id}'
    flag_key: str = f'alive_status:{cache_key}'
    lock_key: str = f'lock:{flag_key}'

    with RedisInterface.pipeline() as pipe:
        pipe.get(flag_key)
        pipe.get(lock_key)
        latest_intent, lock = pipe.execute()
    
    if latest_intent == RedisConfig.RESOURCE_CREATION_PENDING_FLAG or lock:
        raise Conflict('A request for recovering this account is already underway')
    if not deadAccount.deleted or latest_intent == RedisConfig.RESOURCE_DELETION_PENDING_FLAG:  # If both cache and DB fail to verify the prior deletion of this account, raise Conflict
        raise Conflict('Account not deleted')
    
    lock_set = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock_set:
        raise Conflict('A request for recovering this account is already underway')
    
    # Account recovery good to go (TODO: Change this to an xadd to a dedicated stream for user recovery)
    try:
        # Write intent as account recovery (represented by the usual creation flag), add user ID to account recovery stream, and write user mapping to cache
        with RedisInterface.pipeline(transaction=False) as pipe:
            pipe.set(flag_key, RedisConfig.RESOURCE_CREATION_PENDING_FLAG, ex=RedisConfig.TTL_STRONGEST)
            pipe.xadd('USER_ACTIVITY_RECOVERY', {'user_id' : deadAccount.id, 'rtbf' : int(deadAccount.rtbf), 'table' : User.__tablename__})
            pipe.hset(cache_key, user_mapping)
            pipe.expire(cache_key, RedisConfig.TTL_STRONG)
            pipe.execute()
        enqueueEmail(RedisInterface, email=deadAccount.email, subject="recovery", username=deadAccount.username, user_id=deadAccount.id, time_restored=datetime.now().isoformat())
    except:
        raise InternalServerError("Failed to recover your account. If this issue persists, please raise a user ticket immediately")
    finally:
        RedisInterface.delete(lock_key)

    return jsonify({"message" : "Account recovered succesfully",
                   "user" : user_mapping,
                   "_links" : {"login" : {"href" : url_for(".login")}}}), 200

@USERS_BLUEPRINT.route("/recover-password", methods = ["POST"])
@enforce_json
def recover_password() -> Response:
    if not g.REQUEST_JSON.get('identity'):
        raise BadRequest("Missing identity for password recovery")
    
    isEmail: bool = '@' in g.REQUEST_JSON['identity']
    OP, USER_DETAIL = processUserInfo(email = g.REQUEST_JSON.get('identity') if isEmail else None,
                                      username = g.REQUEST_JSON.get('identity') if not isEmail else None)
    
    if not OP:
        raise BadRequest(USER_DETAIL["error"])

    recoveryAccount = db.session.execute(select(User)
                                         .where(User.email == USER_DETAIL.get('email') if isEmail else User.username == USER_DETAIL['username'])
                                         ).scalar_one_or_none()

    if not recoveryAccount:
        raise NotFound(f"No account with this {'email' if isEmail else 'username'} exists")
    
    temp_url = (uuid4().hex + datetime.now().strftime('%d%m%y%H%M%S'))
    hashedToken = sha256(temp_url.encode()).digest()
    try:
        db.session.execute(delete(PasswordRecoveryToken)
                           .where(PasswordRecoveryToken.user_id == recoveryAccount.id))
        db.session.execute(insert(PasswordRecoveryToken)
                           .values(user_id = recoveryAccount.id,
                                   expiry = datetime.now() + current_app.config['PASSWORD_TOKEN_MAX_AGE'],
                                   url_hash = hashedToken))
        db.session.commit()
    except:
        raise InternalServerError("There seems to be an issue with our password recovery service")
    enqueueEmail(RedisInterface, email=recoveryAccount.email, subject="password", username=recoveryAccount.username, password_recovery_link=url_for('templates.recover_password', _external=True, digest=temp_url))
    return jsonify({"message" : "An email has been sent to account"}), 200    

@USERS_BLUEPRINT.route("/update-password/<string:temp_url>", methods=["PATCH"])
@enforce_json
def update_password(temp_url: str) -> Response:
    if len(temp_url) < 15:
        raise BadRequest("Invalid token")
    
    if not (g.REQUEST_JSON.get('password') and g.REQUEST_JSON.get('cpassword')):
        raise BadRequest("Request requires new password")
    
    if g.REQUEST_JSON['password'] != g.REQUEST_JSON['cpassword']:
        raise BadRequest("Passwords do not match")
    
    OP, DETAILS = processUserInfo(password = g.REQUEST_JSON['password'])
    if not OP:
        raise BadRequest(DETAILS.get('error', "Invalid password"))
    
    hashedToken: bytes = sha256(temp_url.encode()).digest()
    dbToken: PasswordRecoveryToken = db.session.execute(select(PasswordRecoveryToken)
                                                        .where(PasswordRecoveryToken.url_hash == hashedToken)
                                                        ).scalar_one_or_none()
    if not dbToken:
        raise NotFound("No such token exists. Please retry")
    
    if datetime.now() > dbToken.expiry:
        return jsonify({"message" : "Token expired, please try again"}), 410
    
    pw_hash, pw_salt = hash_password(DETAILS['password'])
    try:
        user = db.session.execute(update(User)
                                  .where(User.id == dbToken.user_id)
                                  .values(pw_hash = pw_hash, pw_salt = pw_salt)
                                  .returning(User.id)
                                  ).scalar_one_or_none()
        if not user:
            raise NotFound("Account not found")
        
        db.session.execute(delete(PasswordRecoveryToken)
                           .where(PasswordRecoveryToken.user_id == dbToken.user_id))
        db.session.commit()
    except SQLAlchemyError:
        raise InternalServerError("Failed to recover password")
    
    return jsonify({"message" : "password updated succesfully",
                    "_links" : {"login" : {"href" : url_for("templates.login")}}})

@USERS_BLUEPRINT.route("/<int:user_id>", methods=["GET"])
def get_user(user_id: int) -> tuple[Response, int]:
    cacheKey: str = f'user:{user_id}'
    userMapping: dict[str, str|int] = consult_cache(RedisInterface, cacheKey, RedisConfig.TTL_CAP, RedisConfig.TTL_PROMOTION,RedisConfig.TTL_EPHEMERAL)
    global_posts_count, global_comments_count = fetch_global_counters(RedisInterface, f'{cacheKey}:total_posts', f'{cacheKey}:total_comments')

    if userMapping:
        if RedisConfig.NF_SENTINEL_KEY in userMapping:
            raise NotFound('No user with this ID exists')
        
        # Update with global counters
        if global_comments_count is not None:
            userMapping['comments'] = global_comments_count
        if global_posts_count is not None:
            userMapping['posts'] = global_posts_count
        return jsonify({'user' : userMapping})        

    # Fallback to DB
    try:
        user: User = db.session.execute(select(User)
                                        .where((User.id == user_id) & (User.deleted.isnot(False)))
                                        ).scalar_one_or_none()
        if not user:
            hset_with_ttl(RedisInterface, cacheKey, {RedisConfig.NF_SENTINEL_KEY : RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
            raise NotFound('No user with this ID exists')
        
        userMapping = rediserialize(user.__json_like__())
    except SQLAlchemyError: genericDBFetchException()

    # Update with global counters
    if global_comments_count is not None:
        userMapping['comments'] = global_comments_count
    if global_posts_count is not None:
        userMapping['posts'] = global_posts_count
    hset_with_ttl(RedisInterface, cacheKey, userMapping, RedisConfig.TTL_STRONG)
    return jsonify({'user' : userMapping}), 200

@USERS_BLUEPRINT.route('/<int:user_id>/posts')
def get_user_posts(user_id: int) -> tuple[Response, int]:
    try:
        raw_cursor = request.args.get('cursor', '0').strip()
        if raw_cursor == '0':
            cursor: int = 0
        else:
            cursor = int(base64.b64decode(raw_cursor).decode())
    except (ValueError, TypeError):
            raise BadRequest("Failed to load more posts. Please try again later")

    cache_key: str = f'{User.__tablename__}:{user_id}'
    pagination_cache_key: str = f'{cache_key}:{Post.__tablename__}:{cursor}'
    user_mapping: dict[str, Any] = resource_existence_cache_precheck(client=RedisInterface, identifier=user_id, resource_name=User.__tablename__, cache_key=cache_key)

    if not user_mapping:
        # Ensure user exists before trying to fetch their posts
        try:
            user: User = db.session.execute(select(User)
                                            .where((User.id == user_id) & (User.deleted.is_(False)))
                                            ).scalar_one_or_none()
            if not user:
                hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
                raise NotFound(f'No user with ID {user_id} could be found')
            user_mapping: dict[str, Any] = user.__json_like__()
        except SQLAlchemyError: genericDBFetchException()
    
    posts, end, next_cursor = fetch_group_resources(RedisInterface, group_key=pagination_cache_key)
    counter_attrs: list[str] = ['score', 'total_comments', 'saves']
    if posts and all(posts):
        counters_mapping: dict[str, Sequence[int|None]] = fetch_global_counters(client=RedisInterface, hashmaps=[f'{Post.__tablename__}:{attr}' for attr in counter_attrs], identifiers=[post['id'] for post in posts])
        for idx, (attribute, counters) in enumerate(counters_mapping.items()):
            posts[idx][attribute] = counters[idx]
        # Return paginated result with updated counters
        promote_group_ttl(RedisInterface, group_key=pagination_cache_key, promotion_ttl=RedisConfig.TTL_PROMOTION, max_ttl=RedisConfig.TTL_CAP)
        return jsonify({'posts' : posts, 'cursor' : next_cursor, 'end' : end}), 200
    # Cache miss
    try:
        whereClause = (Post.author_id == user_id)
        if cursor:      # Cursor value at 0 will evaluate to False
            whereClause &= (Post.id < cursor)
        next_posts: list[Post] = db.session.execute(select(Post)
                                                    .where(whereClause)
                                                    .order_by(Post.time_posted.desc())
                                                    .limit(6)
                                                    ).scalars().all()
    except SQLAlchemyError: genericDBFetchException()
    if not next_posts:
        return jsonify({'posts' : None, 'end' : True, 'cursor' : cursor})
    
    end: bool = len(next_posts) < 6
    if not end:
        next_posts.pop(-1)
    next_cursor: str = base64.b64encode(str(next_posts[-1].id).encode('utf-8')).decode()

    jsonified_posts: list[dict[str, Any]] = [post.__json_like__() | {'username' : user_mapping['username']} for post in next_posts]
    # Cache grouped resources with updated counters
    cache_grouped_resource(RedisInterface, group_key=pagination_cache_key,
                           resource_type=Post.__tablename__, resources={jsonified_post['id'] : rediserialize(jsonified_post) for jsonified_post in jsonified_posts},
                           weak_ttl=RedisConfig.TTL_WEAK, strong_ttl=RedisConfig.TTL_STRONG,
                           cursor=next_cursor, end=end)

    return jsonify({'posts' : jsonified_posts, 'cursor' : next_cursor, 'end' : end})

@USERS_BLUEPRINT.route('/<int:user_id>/forums')
def get_user_forums(user_id: int) -> tuple[Response, int]:
    try:
        raw_cursor = request.args.get('cursor', '0').strip()
        if raw_cursor == '0':
            cursor: int = 0
        else:
            cursor = int(base64.b64decode(raw_cursor).decode())
    except (ValueError, TypeError):
            raise BadRequest("Failed to load more forums. Please try again")

    cache_key: str = f'{User.__tablename__}:{user_id}'
    pagination_cache_key: str = f'{cache_key}:{Forum.__tablename__}:{cursor}'
    user_mapping: dict[str, Any] = resource_existence_cache_precheck(client=RedisInterface, identifier=user_id, resource_name=User.__tablename__, cache_key=cache_key)

    if not user_mapping:
        # Ensure user exists before trying to fetch their subscribed forums
        try:
            user: User = db.session.execute(select(User)
                                            .where((User.id == user_id) & (User.deleted.is_(False)))
                                            ).scalar_one_or_none()
            if not user:
                hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
                raise NotFound(f'No user with ID {user_id} could be found')
            user_mapping: dict[str, Any] = user.__json_like__()
        except SQLAlchemyError: genericDBFetchException()
    
    forums, end, next_cursor = fetch_group_resources(RedisInterface, group_key=pagination_cache_key)
    counter_attrs: list[str] = ['subscribers', 'posts', 'admin_count']
    if forums and all(forums):
        counters_mapping: dict[str, Sequence[int|None]] = fetch_global_counters(client=RedisInterface, hashmaps=[f'{Post.__tablename__}:{attr}' for attr in counter_attrs], identifiers=[forum['id'] for forum in forums])
        for idx, (attribute, counters) in enumerate(counters_mapping.items()):
            forums[idx][attribute] = counters[idx]
        # Return paginated result with updated counters
        promote_group_ttl(RedisInterface, group_key=pagination_cache_key, promotion_ttl=RedisConfig.TTL_PROMOTION, max_ttl=RedisConfig.TTL_CAP)
        return jsonify({'posts' : forums, 'cursor' : next_cursor, 'end' : end}), 200
    # Cache miss
    try:
        where_clause: BinaryExpression = (Forum.deleted.is_(False))
        if cursor:
            where_clause &= (Forum.id > cursor)
        joined_forum_res: list[tuple[Forum, datetime]] = db.session.execute(select(Forum, ForumSubscription.time_subscribed)
                                                                            .join(ForumSubscription, (Forum.id == ForumSubscription.forum_id) & (ForumSubscription.user_id == user_id))
                                                                            .where(where_clause)
                                                                            .limit(6)
                                                                            .order_by(ForumSubscription.time_subscribed.desc())
                                                                            ).all()
    except SQLAlchemyError: genericDBFetchException()
    if not joined_forum_res:
        return jsonify({'forums' : None, 'end' : True, 'cursor' : cursor})
    
    end: bool = len(joined_forum_res) < 6
    if not end:
        joined_forum_res.pop(-1)
    next_cursor: str = base64.b64encode(str(joined_forum_res[-1][0].id).encode('utf-8')).decode()
    jsonified_forums: list[dict[str, Any]] = [res[0].__json_like__() | {'time_subscribed' : res[1]} for res in joined_forum_res]

    # Cache grouped resources with updated counters
    cache_grouped_resource(RedisInterface, group_key=pagination_cache_key,
                           resource_type=Post.__tablename__, resources={jsonified_forum['id'] : rediserialize(jsonified_forum) for jsonified_forum in jsonified_forums},
                           weak_ttl=RedisConfig.TTL_WEAK, strong_ttl=RedisConfig.TTL_STRONG,
                           cursor=next_cursor, end=end)

    return jsonify({'forums' : jsonified_forums, 'cursor' : next_cursor, 'end' : end})

@USERS_BLUEPRINT.route('/<int:user_id>/animes')
def get_user_animes(user_id: int) -> tuple[Response, int]:
    try:
        raw_cursor = request.args.get('cursor', '0').strip()
        if raw_cursor == '0':
            cursor: int = 0
        else:
            cursor = int(base64.b64decode(raw_cursor).decode())
    except (ValueError, TypeError):
            raise BadRequest("Failed to load more posts. Please try again later")

    cache_key: str = f'{User.__tablename__}:{user_id}'
    pagination_cache_key: str = f'{cache_key}:{Anime.__tablename__}:{cursor}'
    user_mapping: dict[str, Any] = resource_existence_cache_precheck(client=RedisInterface, identifier=user_id, resource_name=User.__tablename__, cache_key=cache_key)

    if not user_mapping:
        # Ensure user exists before trying to fetch their posts
        try:
            user: User = db.session.execute(select(User)
                                            .where((User.id == user_id) & (User.deleted.is_(False)))
                                            ).scalar_one_or_none()
            if not user:
                hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
                raise NotFound(f'No user with ID {user_id} could be found')
            user_mapping: dict[str, Any] = user.__json_like__()
        except SQLAlchemyError: genericDBFetchException()
    
    animes, end, next_cursor = fetch_group_resources(RedisInterface, group_key=pagination_cache_key)
    counter_attrs: list[str] = ['members']
    if animes and all(animes):
        counters_mapping: dict[str, Sequence[int|None]] = fetch_global_counters(client=RedisInterface, hashmaps=[f'{Post.__tablename__}:{attr}' for attr in counter_attrs], identifiers=[anime['id'] for anime in animes])
        for idx, (attribute, counters) in enumerate(counters_mapping.items()):
            animes[idx][attribute] = counters[idx]
        # Return paginated result with updated counters
        promote_group_ttl(RedisInterface, group_key=pagination_cache_key, promotion_ttl=RedisConfig.TTL_PROMOTION, max_ttl=RedisConfig.TTL_CAP)
        return jsonify({'animes' : animes, 'cursor' : next_cursor, 'end' : end}), 200
    
    where_clause: BinaryExpression = (AnimeSubscription.user_id == user_id)
    if cursor:
        where_clause &= (Anime.id > cursor)
    # Cache miss
    try:
        next_anime_res: list[tuple[Anime, datetime]] = db.session.execute(select(Anime, AnimeSubscription.time_subscribed)
                                                                          .join(AnimeSubscription, AnimeSubscription.anime_id == Anime.id)
                                                                          .where(where_clause)
                                                                          .limit(6)
                                                                          .order_by(AnimeSubscription.time_subscribed.desc())
                                                                          ).all()
    except SQLAlchemyError: genericDBFetchException()
    if not next_anime_res:
        return jsonify({'animes' : None, 'end' : True, 'cursor' : raw_cursor})
    end: bool = len(next_anime_res) < 6
    if not end:
        next_anime_res.pop(-1)
    next_cursor: str = base64.b64encode(str(next_anime_res[-1][0].id).encode('utf-8')).decode()
    jsonified_animes: list[dict[str, Any]] = [row[0].__json_like__() | {'time_subscribed' :row[1].isoformat()} for row in next_anime_res]
    # Cache grouped resources with updated counters
    cache_grouped_resource(RedisInterface, group_key=pagination_cache_key,
                           resource_type=Anime.__tablename__, resources={jsonified_anime['id'] : jsonified_anime for jsonified_anime in jsonified_animes},
                           weak_ttl=RedisConfig.TTL_WEAK, strong_ttl=RedisConfig.TTL_STRONG,
                           cursor=next_cursor, end=end)

    return jsonify({'animes' : jsonified_animes, 'cursor' : next_cursor, 'end' : end})

@USERS_BLUEPRINT.route("/login", methods=["POST"])
# @private
@enforce_json
def login() -> Response:
    if not(g.REQUEST_JSON.get('identity') and g.REQUEST_JSON.get('password')):
        raise BadRequest("Login requires email/username and password to be provided")
    
    identity : str = g.REQUEST_JSON['identity'].strip()

    isEmail = False
    if "@" in identity:
        if not 5 < len(identity) < 320:
            raise BadRequest("Provided email must be between 5 and 320 characters long")
        isEmail = True
    else:
        if not 5 < len(identity) <= 64:
            raise BadRequest("Provided username must be between 5 and 64 characters long")
    # NOTE: We can't do a cache precheck since that requires user ID, and we don't have that yet
    try:
        user: User = db.session.execute(select(User)
                                        .where((User.email == identity if isEmail else User.username == identity) & (User.deleted.is_(False)))
                                        .with_for_update()
                                        ).scalar_one_or_none()
    except SQLAlchemyError: genericDBFetchException()
    if not user:
        raise NotFound(f"No user with {'email' if isEmail else 'username'} {identity} could be found")
    
    # Even if account found, check cache for deletion intent
    deletion_intent_flag: str = f'alive_status:{user.id}'
    deletion_intent = RedisInterface.get(deletion_intent_flag)
    if deletion_intent == RedisConfig.RESOURCE_DELETION_PENDING_FLAG:
        gone: Gone = Gone('This account has been deleted. If you wish to undo this, please visit account recovery')
        gone.kwargs = {'links' : {'account recovery' : {'_href' : url_for('.recover_user', _external=True)}}}
        raise gone
    # Account exists and is not queued for deletion
    if not verify_password(g.REQUEST_JSON['password'], user.pw_hash, user.pw_salt):
        raise Unauthorized("Incorrect password")
    
    epoch = datetime.now()
    try:
        db.session.execute(update(User)
                           .where(User.id == user.id)
                           .values(last_login = epoch))
        db.session.commit()
    except:
        db.session.rollback()
        raise InternalServerError('Failed to login, please try again later')

    # Communicate with auth server
    return jsonify({"message" : "authorization successful",
                    "sub" : user.username,
                    "sid" : user.id}), 200

@USERS_BLUEPRINT.route('/<int:user_id>/enable-rtbf', methods=['PATCH'])
@enforce_json
@token_required
def enable_rtbf(user_id: int) -> tuple[Response, int]:
    if user_id != g.DECODED_TOKEN['sid']:
        raise Forbidden("Missing permissions to edit this user's data")
    
    confirmation_text: str = g.REQUEST_JSON.pop('confirmation', None)
    if confirmation_text != g.DECODED_TOKEN['sub']:
        raise Forbidden('Invalid confirmation text, please try again')
    
    user_rtbf: bool = db.session.execute(select(User.rtbf)
                                         .where(User.id == user_id)
                                         .with_for_update(nowait=True)
                                         ).scalar_one_or_none()
    if user_rtbf is None:
        raise NotFound('This account could not be accessed at the moment')
    if user_rtbf:
        raise Conflict('RTBF already enabled for this account')
    
    try:
        db.session.execute(update(User)
                           .where(User.id == user_id)
                           .values(rtbf=True))
        db.session.commit()
    except SQLAlchemyError: raise InternalServerError("Failed to update RTBF settings at the moment, please try again later") 
    
    return jsonify({'Message' : 'RTBF Enabled'}), 200

@USERS_BLUEPRINT.route('/<int:user_id>/disable-rtbf', methods=['PATCH'])
@enforce_json
@token_required
def disable_rtbf(user_id: int) -> tuple[Response, int]:
    if user_id != g.DECODED_TOKEN['sid']:
        raise Forbidden("Missing permissions to edit this user's data")
    
    confirmation_text: str = g.REQUEST_JSON.pop('confirmation', None)
    if confirmation_text != g.DECODED_TOKEN['sub']:
        raise Forbidden('Invalid confirmation text, please try again')
    
    user_rtbf: bool = db.session.execute(select(User.rtbf)
                                         .where(User.id == user_id)
                                         .with_for_update(nowait=True)
                                         ).scalar_one_or_none()
    if user_rtbf is None:
        raise NotFound('This account could not be accessed at the moment')
    if not user_rtbf:
        raise Conflict('RTBF already disabled for this account')
    try:
        db.session.execute(update(User)
                           .where(User.id == user_id)
                           .values(rtbf=False))
        db.session.commit()
    except SQLAlchemyError: raise InternalServerError("Failed to updatwe RTBF settings at the moment, please try again later") 
    
    return jsonify({'Message' : 'RTBF Disabled'}), 200
