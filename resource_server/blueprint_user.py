'''Blueprint module for all actions related to resource: users'''
from flask import Blueprint, g, request, jsonify, current_app, url_for
from werkzeug import Response
from werkzeug.exceptions import BadRequest, Conflict, InternalServerError, NotFound, Unauthorized, Forbidden
from auxillary.decorators import enforce_json
from auxillary.utils import hash_password, verify_password, genericDBFetchException, rediserialize, consult_cache, fetch_group_resources, promote_group_ttl, cache_grouped_resource
from resource_server.resource_auxillary import processUserInfo, fetch_global_counters, pipeline_exec
from resource_server.external_extensions import RedisInterface, hset_with_ttl
from resource_server.resource_decorators import token_required
from resource_server.models import db, User, PasswordRecoveryToken, Post, Forum, ForumSubscription, Anime, AnimeSubscription
from resource_server.scripts.mail import enqueueEmail
from resource_server.redis_config import RedisConfig
from redis.client import Pipeline
from sqlalchemy import select, insert, update, delete
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from datetime import datetime
from redis.exceptions import RedisError
from uuid import uuid4
import base64
from hashlib import sha256

user = Blueprint(__file__.split(".")[0], __file__.split(".")[0], url_prefix="/users")

@user.route("/", methods=["POST"])
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
 
@user.route("/", methods=["DELETE"])
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
        pipeline_exec(RedisInterface, op_mapping={Pipeline.set : {'name' : flag_key, 'value' : RedisConfig.RESOURCE_DELETION_PENDING_FLAG, 'ex' : RedisConfig.TTL_STRONGEST},
                                                  Pipeline.xadd : {'name' : 'USER_ACTIVITY_DELETIONS', 'fields' : {'user_id' : targetUser.id, 'rtbf' : int(targetUser.rtbf), 'table' : User.__tablename__}},
                                                  Pipeline.hset : {'name' : cache_key, 'mapping' : {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}},
                                                  Pipeline.expire : {'name' : cache_key, 'time' : RedisConfig.TTL_WEAK}})
        enqueueEmail(RedisInterface, email=targetUser.email, subject='deletion', username=targetUser.username)
    except RedisError: 
        raise InternalServerError("Failed to perform account deletion, please try again. If the issue persists, please raise a ticket")
    finally:
        RedisInterface.delete(lock_key)
    
    return jsonify({"message" : "account deleted succesfully", "username" : user.username, "time_deleted" : user.time_deleted}), 203

@user.route("/recover", methods=["PATCH"])
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
        pipeline_exec(RedisInterface, op_mapping={Pipeline.set : {'name' : flag_key, 'value' : RedisConfig.RESOURCE_CREATION_PENDING_FLAG, 'ex' : RedisConfig.TTL_STRONGEST},
                                                  Pipeline.xadd : {'name' : 'USER_ACTIVITY_RECOVERY', 'fields' : {'user_id' : deadAccount.id, 'rtbf' : int(deadAccount.rtbf), 'table' : User.__tablename__}},
                                                  Pipeline.hset : {'name' : cache_key, 'mapping' : user_mapping},
                                                  Pipeline.expire : {'name' : cache_key, 'time' : RedisConfig.TTL_STRONG}})
        enqueueEmail(RedisInterface, email=deadAccount.email, subject="recovery", username=deadAccount.username, user_id=deadAccount.id, time_restored=datetime.now().isoformat())
    except:
        raise InternalServerError("Failed to recover your account. If this issue persists, please raise a user ticket immediately")
    finally:
        RedisInterface.delete(lock_key)

    return jsonify({"message" : "Account recovered succesfully",
                   "user" : user_mapping,
                   "_links" : {"login" : {"href" : url_for(".login")}}}), 200

@user.route("/recover-password", methods = ["POST"])
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

@user.route("/update-password/<string:temp_url>", methods=["PATCH"])
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

@user.route("/<int:user_id>", methods=["GET"])
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

@user.route('/<int:user_id>/posts')
def get_user_posts(user_id: int) -> tuple[Response, int]:
    try:
        rawCursor = request.args.get('cursor', '0').strip()
        if rawCursor == '0':
            cursor: int = 0
        else:
            cursor = int(base64.b64decode(rawCursor).decode())
    except (ValueError, TypeError):
            raise BadRequest("Failed to load more posts. Please refresh this page")
    
    # 1: Redis
    cacheKey: str = f'profile:{user_id}:posts:{cursor}'     # cacheKey here will just be a list of key names for individually cached posts
    counter_names: list[str] = []
    global_counters: list[int] = []

    resources, end, nextCursor = fetch_group_resources(RedisInterface, cacheKey)
    if resources and all(resources):
        for resource in resources:  # Prep name list in advance
            counter_names.extend([f'post:{resource["id"]}:score', f'forum:{resource["id"]}:saves', f'forum:{resource["id"]}:total_comments'])
        
        global_counters = fetch_global_counters(RedisInterface, *counter_names)
        post_idx: int = 0
        # Update with global counters
        for i in range(0, len(global_counters), 3):
            if global_counters[i] is not None:  # Post score
                resources[post_idx]['score'] = global_counters[i]
            if global_counters[i+1] is not None:  # Post saves
                resources[post_idx]['saves'] = global_counters[i+1]
            if global_counters[i+2] is not None:  # Post comments
                resources[post_idx]['comments'] = global_counters[i+2]
            post_idx+=1
        # All resources exist in cache, promote TTL and return results
        promote_group_ttl(interface=RedisInterface, group_key=cacheKey,
                          promotion_ttl=RedisConfig.TTL_PROMOTION, max_ttl=RedisConfig.TTL_CAP)
        return jsonify({'posts' : resources, 'cursor' : nextCursor, 'end' : end})
    
    # Cache failure, either missing key in set or set does not exist. Either way, we'll have to bother the DB >:3
    try:
        user: User = db.session.execute(select(User)
                                        .where((User.id == user_id) & (User.deleted.is_(False)))
                                        ).scalar_one_or_none()
        if not user:
            # Broadcast non-existence
            hset_with_ttl(RedisInterface, f'user:{user_id}', {"__NF__" : -1}, RedisConfig.TTL_EPHEMERAL)
            raise NotFound('No user with this ID exists')
        
        whereClause = (Post.author_id == user_id)
        if cursor:      # Cursor value at 0 will evaluate to False
            whereClause &= (Post.id < cursor)
        
        recentPosts: list[Post] = db.session.execute(select(Post)
                                                     .where(whereClause)
                                                     .order_by(Post.time_posted.desc())
                                                     .limit(6)
                                                     ).scalars().all()
    except SQLAlchemyError: genericDBFetchException()

    if not recentPosts:
        with RedisInterface.pipeline() as pipe:
            pipe.lpush(cacheKey, RedisConfig.NF_SENTINEL_KEY)  # Announce non-existence
            pipe.expire(cacheKey, RedisConfig.TTL_WEAK)
            pipe.execute()

        return jsonify({'posts' : None, 'cursor' : None, 'end' : True})
    
    end: bool = len(recentPosts) < 6
    if not end:
        recentPosts.pop(-1)

    nextCursor = base64.b64encode(str(recentPosts[-1].id).encode('utf-8')).decode()
    _posts = [rediserialize(post.__json_like__()) for post in recentPosts]

    for post in _posts:  # Prep name list in advance
        counter_names.extend([f'post:{post["id"]}:score', f'forum:{post["id"]}:saves', f'forum:{post["id"]}:total_comments'])
        
        global_counters = fetch_global_counters(RedisInterface, *counter_names)
        post_idx: int = 0
        # Update with global counters
        for i in range(0, len(global_counters), 3):
            if global_counters[i] is not None:  # Post score
                _posts[post_idx]['score'] = global_counters[i]
            if global_counters[i+1] is not None:  # Post saves
                _posts[post_idx]['saves'] = global_counters[i+1]
            if global_counters[i+2] is not None:  # Post comments
                _posts[post_idx]['comments'] = global_counters[i+2]
            post_idx+=1

    # Cache grouped resources with updated counters
    cache_grouped_resource(interface=RedisInterface,
                           group_key=cacheKey, resource_type='post',
                           resources= {post['id']:post for post in _posts},
                           weak_ttl= RedisConfig.TTL_WEAK, strong_ttl= RedisConfig.TTL_STRONG,
                           cursor=nextCursor, end=end)

    return jsonify({'posts' : _posts, 'cursor' : nextCursor, 'end' : end})

@user.route('/<int:user_id>/forums')
def get_user_forums(user_id: int) -> tuple[Response, int]:
    try:
        rawCursor = request.args.get('cursor', '0').strip()
        if rawCursor == '0':
            cursor: int = 0
        else:
            cursor = int(base64.b64decode(rawCursor).decode())
    except (ValueError, TypeError):
            raise BadRequest("Failed to load more Forums. Please refresh this page")

    cacheKey: str = f'profile:{user_id}:forums:{cursor}'
    counter_names: list[str] = []
    global_counters: list[int] = []

    resources, end, newCursor = fetch_group_resources(RedisInterface, cacheKey)
    if resources and all(resources):
        # Group cache is valid
        # Fetch updated global counters if in memory
        for resource in resources:  # Prep name list in advance
            counter_names.extend([f'forum:{resource["id"]}:posts', f'forum:{resource["id"]}:subscribers', f'forum:{resource["id"]}:admin_count'])

        global_counters = fetch_global_counters(RedisInterface, *counter_names)
        forum_idx: int = 0
        # Update with global counters
        for i in range(0, len(global_counters), 3):
            if global_counters[i] is not None:  # Forum posts
                resources[forum_idx]['posts'] = global_counters[i]
            if global_counters[i+1] is not None:  # Forum subscribers
                resources[forum_idx]['subscribers'] = global_counters[i+1]
            if global_counters[i+2] is not None:  # Forum admins
                resources[forum_idx]['admins'] = global_counters[i+2]
            forum_idx+=1

        promote_group_ttl(RedisInterface, cacheKey, promotion_ttl=RedisConfig.TTL_PROMOTION, max_ttl=RedisConfig.TTL_CAP)
        return jsonify({'forums' : resources, 'cursor' : newCursor, 'end' : end}), 200

    try:
        userID: int = db.session.execute(select(User.id)
                                         .where((User.id == user_id) & (User.deleted.is_(False)))
                                         ).scalar_one_or_none()
        if not userID:
            hset_with_ttl(RedisInterface, f'user:{user_id}', {"__NF__" : -1}, RedisConfig.TTL_EPHEMERAL)
            raise NotFound(f'No user with ID {userID} exists')
        
        forums: list[Forum] = db.session.execute(select(Forum)
                                                 .join(ForumSubscription, ForumSubscription.user_id == userID)
                                                 .where((Forum.id == ForumSubscription.forum_id) & (Forum.id > cursor))
                                                 .limit(6)
                                                 ).scalars().all()
    except SQLAlchemyError: genericDBFetchException()

    if not forums:
        with RedisInterface.pipeline() as pipe:
            pipe.lpush(cacheKey, RedisConfig.NF_SENTINEL_KEY)
            pipe.expire(cacheKey, RedisConfig.TTL_EPHEMERAL)
            pipe.execute()
        return jsonify({'forums' : None, 'cursor' : cursor, 'end' : True})

    end: bool = len(forums) < 6
    if not end:
        forums.pop(-1)

    newCursor: str = base64.b64encode(str(forums[-1].id).encode('utf-8')).decode()
    _forums = [rediserialize(forum.__json_like__()) for forum in forums]

    # Update _forums with global counters
    for forum in _forums:
        counter_names.extend([f'forum:{forum["id"]}:posts', f'forum:{forum["id"]}:subscribers', f'forum:{forum["id"]}:admin_count'])
    global_counters = fetch_global_counters(RedisInterface, *counter_names)
    forum_idx: int = 0
    # Update with global counters
    for i in range(0, len(global_counters), 3):
        if global_counters[i] is not None:  # Forum posts
            _forums[forum_idx]['posts'] = global_counters[i]
        if global_counters[i+1] is not None:  # Forum subscribers
            _forums[forum_idx]['subscribers'] = global_counters[i+1]
        if global_counters[i+2] is not None:  # Forum admins
            _forums[forum_idx]['admins'] = global_counters[i+2]
        forum_idx+=1

    # Group cache with updated counters
    cache_grouped_resource(RedisInterface, cacheKey, 'forum', {forum['id']:forum for forum in _forums},
                           RedisConfig.TTL_WEAK, RedisConfig.TTL_STRONG,
                           cursor=newCursor, end=end)
    
    return jsonify({'forums' : _forums, 'cursor' : newCursor, 'end' : end})

@user.route('/<int:user_id>/animes')
def get_user_animes(user_id: int) -> tuple[Response, int]:
    try:
        rawCursor = request.args.get('cursor', '0').strip()
        if rawCursor == '0':
            cursor: int = 0
        else:
            cursor = int(base64.b64decode(rawCursor).decode())
    except (ValueError, TypeError):
            raise BadRequest("Failed to load more animes. Please refresh this page")
    
    cacheKey: str = f'profile:{user_id}:animes:{cursor}'
    counter_names: list[str] = []
    global_counters: list[int] = []

    resources, end, newCursor = fetch_group_resources(RedisInterface, cacheKey)
    if resources and all(resources):
        # Fetch updated global counters if in memory
        for resource in resources:  # Prep name list in advance
            counter_names.append(f'anime:{resource["id"]}:members')

        global_counters = fetch_global_counters(RedisInterface, *counter_names)
        # Update with global counters
        for anime_idx, counter in enumerate(global_counters):
            if counter is not None:  # Anime members
                resources[anime_idx]['members'] = counter
            anime_idx+=1

        promote_group_ttl(RedisInterface, cacheKey, RedisConfig.TTL_PROMOTION, RedisConfig.TTL_CAP)
        return jsonify({'animes' : resources, 'cursor' : newCursor, 'end' : end}), 200

    try:
        user: User = db.session.execute(select(User)
                                        .where((User.id == user_id) & (User.deleted.is_(False)))
                                        ).scalar_one_or_none()
        if not user:
            hset_with_ttl(RedisInterface, f'user:{user_id}', {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
            raise NotFound('No user with this ID exists')
        
        animes: list[Anime] = db.session.execute(select(Anime)
                                                .join(AnimeSubscription, AnimeSubscription.user_id == user.id)
                                                .where((Anime.id == AnimeSubscription.anime_id) & (Anime.id > cursor))
                                                .limit(6)).scalars().all()
        if not animes:
            return jsonify({'animes' : None, 'cursor' : cursor, 'end' : True})
        
        end: bool = True
        if len(animes) >= 6:
            end = False
            animes.pop(-1)
    except SQLAlchemyError: genericDBFetchException()

    newCursor: str = base64.b64encode(str(animes[-1].id).encode('utf-8')).decode()
    _animes = [rediserialize(anime.__json_like__()) for anime in animes]

    for anime in _animes:  # Prep name list in advance
        counter_names.append(f'anime:{anime["id"]}:members')

        global_counters = fetch_global_counters(RedisInterface, *counter_names)
        # Update with global counters
        for anime_idx, counter in enumerate(global_counters):
            if counter is not None:  # Anime members
                _animes[anime_idx]['members'] = counter
            anime_idx+=1

    # Cache as a group with updated counters
    cache_grouped_resource(RedisInterface, cacheKey, 'anime', {anime['id']:anime for anime in _animes}, RedisConfig.TTL_WEAK, RedisConfig.TTL_STRONG, newCursor, end)
    return jsonify({'animes' : _animes, 'cursor' : newCursor, 'end' : end})

@user.route("/login", methods=["POST"])
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

    user: User = db.session.execute(select(User)
                                    .where((User.email == identity if isEmail else User.username == identity) & (User.deleted.is_(False)))
                                    .with_for_update()
                                    ).scalar_one_or_none()
    if not user:
        raise NotFound(f"No user with {'email' if isEmail else 'username'} {identity} could be found")
    if not verify_password(g.REQUEST_JSON['password'], user.pw_hash, user.pw_salt):
        raise Unauthorized("Incorrect password")
    
    epoch = datetime.now()
    db.session.execute(update(User).where(User.id == user.id).values(last_login = epoch))
    db.session.commit()

    # Communicate with auth server
    return jsonify({"message" : "authorization successful",
                    "sub" : user.username,
                    "sid" : user.id}), 200

@user.route('/<int:user_id>/enable-rtbf', methods=['PATCH'])
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

@user.route('/<int:user_id>/disable-rtbf', methods=['PATCH'])
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
