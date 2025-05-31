'''Blueprint module for all actions related to resource: user (`users` in database)
### Note: All user related actions (account creation, User.last_login updation, and account deletion are done directly without query dispatching to Redis)
'''
from flask import Blueprint, g, request, jsonify, current_app, url_for
user = Blueprint(__file__.split(".")[0], __file__.split(".")[0], url_prefix="/users")

from werkzeug import Response
from werkzeug.exceptions import BadRequest, Conflict, InternalServerError, NotFound, Unauthorized
from auxillary.decorators import enforce_json
from auxillary.utils import hash_password, verify_password, genericDBFetchException, rediserialize, consult_cache, fetch_group_resources, promote_group_ttl, cache_grouped_resource
from resource_server.resource_auxillary import processUserInfo
from resource_server.external_extensions import RedisInterface, hset_with_ttl
from resource_server.models import db, User, PasswordRecoveryToken, Post, Forum, ForumSubscription, Anime, AnimeSubscription
from resource_server.scripts.mail import enqueueEmail
from sqlalchemy import select, insert, update, delete
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from datetime import datetime
from redis.exceptions import RedisError
from uuid import uuid4
import base64
from hashlib import sha256

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
def delete_user() -> Response:
    if not (g.REQUEST_JSON.get("username") and
            g.REQUEST_JSON.get("password")):
        raise BadRequest("Requires username and password to be provided")
    
    OP, USER_DETAILS = processUserInfo(username=g.REQUEST_JSON['username'], password=g.REQUEST_JSON['password'])
    if not OP:
        raise BadRequest(USER_DETAILS.get("error"))
    targetUser: User = db.session.execute(select(User)
                                     .where((User.username == USER_DETAILS['username']) & (User.deleted.is_(False)))
                                     .with_for_update(nowait=True)
                                     ).scalar_one_or_none()
    if not targetUser:
        raise NotFound("Requested user could not be found")
    
    if not verify_password(g.REQUEST_JSON['password'], targetUser.pw_hash, targetUser.pw_salt):
        raise Unauthorized('Invalid credentials')
    
    try:
        RedisInterface.xadd('SOFT_DELETIONS', {'id' : targetUser.id, 'table' : User.__tablename__})
        # Broadcast user deletion
    except RedisError: 
        raise InternalServerError("Failed to perform account deletion, please try again. If the issue persists, please raise a ticket")
    
    hset_with_ttl(RedisInterface, f'user:{targetUser.id}', {'__NF__' : -1}, current_app.config['REDIS_TTL_WEAK']) # Non ephemeral timing? idk seems right
    enqueueEmail(RedisInterface, email=targetUser.email, subject='deletion', username=targetUser.username)
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
    
    deadAccount = db.session.execute(select(User)
                                     .where((User.email == USER_DETAILS.get('email') if isEmail else User.username == USER_DETAILS['username'] & (User.deleted.is_(True))))
                                     .with_for_update()
                                     ).scalar_one_or_none()

    # Never existed, or hard deleted already
    if not deadAccount:
        nf = NotFound(f"No accounts with this {'email' if isEmail else 'username'} exists.")
        nf.__setattr__("kwargs", {"info" : f"If you had believe that this account is still in the recovery period of {current_app['ACCOUNT_RECOVERY_PERIOD']} days, contact support",
                                  "_links" : {"tickets" : {"href" : url_for("tickets")}}})
        raise nf
    
    # Tough luck, its already past recovery period >:(
    if deadAccount.time_deleted + current_app['ACCOUNT_RECOVERY_PERIOD'] <= datetime.now():
        cnf = Conflict("Account recovery period for this account has already expired, and its deletion is due soon. Please create a new account, or raise a user ticket")
        cnf.__setattr__("kwargs", {"_links" : {"tickets" : {"href" : url_for("tickets")}}})
        raise cnf
    
    # Match password hashes
    if not verify_password(USER_DETAILS['password'], deadAccount.pw_hash, deadAccount.pw_salt):
        unauth = Unauthorized("Incorrect password")
        unauth.__setattr__("kwargs", {"info" : "If you have forgotten your password and want to recover your account, please perform the deleted account password recovery phase", 
                                      "_links" : {"recovery" : {"href" : url_for(".recovery")}}})
        
    # Account recovery good to go
    try:
        db.session.execute(update(User)
                           .where(User.id == deadAccount.id)
                           .values({"deleted" : False, "time_deleted" : None}))
        db.session.commit()
    except:
        raise InternalServerError("Failed to recover your account. If this issue persists, please raise a user ticket immediately")
    
    enqueueEmail(RedisInterface, email=deadAccount.email, subject="recovery", username=deadAccount.username, user_id=deadAccount.id, time_restored=datetime.now().isoformat())
    return jsonify({"message" : "Account recovered succesfully",
                   "username" : deadAccount.username,
                   "email" : deadAccount.email,
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
    print(url_for('templates.recover_password', _external=True, digest=temp_url))
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
def get_users(user_id: int) -> tuple[Response, int]:
    cacheKey: str = f'users:{user_id}'
    userMapping: dict[str, str|int] = consult_cache(RedisInterface, cacheKey, current_app.config['REDIS_TTL_CAP'], current_app.config['REDIS_TTL_PROMOTION'],current_app.config['REDIS_TTL_EPHEMERAL'])

    if userMapping:
        if '__NF__' in userMapping:
            raise NotFound('No user with this ID exists')
        return jsonify({'user' : userMapping})        

    # Fallback to DB
    try:
        user: User = db.session.execute(select(User)
                                        .where((User.id == user_id) & (User.deleted.isnot(False)))
                                        ).scalar_one_or_none()
        if not user:
            hset_with_ttl(RedisInterface, cacheKey, {'__NF__' : -1}, current_app.config['REDIS_TTL_EPHEMERAL'])
            raise NotFound('No user with this ID exists')
        
        userMapping = rediserialize(user.__json_like__())
    except SQLAlchemyError: genericDBFetchException()

    hset_with_ttl(RedisInterface, cacheKey, userMapping, current_app.config['REDIS_TTL_STRONG'])
    return jsonify({'user' : userMapping}), 200

@user.route('profile/<int:user_id>/posts')
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
    resources, end, nextCursor = fetch_group_resources(RedisInterface, cacheKey)
    if resources and all(resources):
        # All resources exist in cache, promote TTL and return results
        promote_group_ttl(interface=RedisInterface, group_key=cacheKey,
                          promotion_ttl=current_app.config['REDIS_TTL_PROMOTION'], max_ttl=current_app.config['REDIS_TTL_CAP'])
        return jsonify({'posts' : resources, 'cursor' : nextCursor, 'end' : end})
    
    # Cache failure, either missing key in set or set does not exist. Either way, we'll have to bother the DB >:3
    try:
        user: User = db.session.execute(select(User)
                                        .where((User.id == user_id) & (User.deleted.is_(False)))
                                        ).scalar_one_or_none()
        if not user:
            # Broadcast non-existence
            hset_with_ttl(RedisInterface, f'user:{user_id}', {"__NF__" : -1}, current_app.config['REDIS_TTL_EPHEMERAL'])
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
            pipe.lpush(cacheKey, '__NF__')  # Announce non-existence
            pipe.expire(cacheKey, current_app.config['REDIS_TTL_WEAK'])
            pipe.execute()

        return jsonify({'posts' : None, 'cursor' : cursor, 'end' : True})
    
    if len(recentPosts) < 6:
        end = True
    else:
        end = False
        recentPosts.pop(-1)

    nextCursor = base64.b64encode(str(recentPosts[-1].id).encode('utf-8')).decode()
    _posts = [rediserialize(post.__json_like__()) for post in recentPosts]

    # Cache grouped resources
    cache_grouped_resource(interface=RedisInterface,
                           group_key=cacheKey, resource_type='post',
                           resources= {post['id']:post for post in _posts},
                           weak_ttl= current_app.config['REDIS_TTL_WEAK'], strong_ttl= current_app.config['REDIS_TTL_STRONG'],
                           cursor=nextCursor, end=end)

    return jsonify({'posts' : _posts, 'cursor' : nextCursor, 'end' : end})

@user.route('profile/<int:user_id>/forums')
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
    resources, end, newCursor = fetch_group_resources(RedisInterface, cacheKey)
    if resources and all(resources):
        # Group cache is valid, promote TTL and dispatch result
        promote_group_ttl(RedisInterface, cacheKey, promotion_ttl=current_app.config['REDIS_TTL_PROMOTION'], max_ttl=current_app.config['REDIS_TTL_CAP'])
        return jsonify({'forums' : resources, 'cursor' : newCursor, 'end' : end}), 200

    try:
        userID: int = db.session.execute(select(User.id)
                                         .where((User.id == user_id) & (User.deleted.is_(False)))
                                         ).scalar_one_or_none()
        if not userID:
            hset_with_ttl(RedisInterface, f'user:{user_id}', {"__NF__" : -1}, current_app.config['REDIS_TTL_EPHEMERAL'])
            raise NotFound(f'No user with ID {userID} exists')
        
        forums: list[Forum] = db.session.execute(select(Forum)
                                                 .join(ForumSubscription, ForumSubscription.user_id == userID)
                                                 .where((Forum.id == ForumSubscription.forum_id) & (Forum.id > cursor))
                                                 .limit(6)
                                                 ).scalars().all()
    except SQLAlchemyError: genericDBFetchException()

    if not forums:
        with RedisInterface.pipeline() as pipe:
            pipe.lpush(cacheKey, '__NF__')
            pipe.expire(cacheKey, current_app.config['REDIS_TTL_EPHEMERAL'])
            pipe.execute()
        return jsonify({'forums' : None, 'cursor' : cursor, 'end' : True})

    end: bool = True
    if len(forums) >= 6:
        end = False
        forums.pop(-1)

    newCursor: str = base64.b64encode(str(forums[-1].id).encode('utf-8')).decode()
    _forums = [rediserialize(forum.__json_like__()) for forum in forums]

    # Group cache
    cache_grouped_resource(RedisInterface, cacheKey, 'forum', {forum['id']:forum for forum in _forums},
                           current_app.config['REDIS_TTL_WEAK'], current_app.config['REDIS_TTL_STRONG'],
                           cursor=newCursor, end=end)
    
    return jsonify({'forums' : _forums, 'cursor' : newCursor, 'end' : end})


@user.route('profile/<int:user_id>/animes')
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
    resource, end, newCursor = fetch_group_resources(RedisInterface, cacheKey)
    if resource and all(resource):
        promote_group_ttl(RedisInterface, cacheKey, current_app.config['REDIS_TTL_PROMOTION'], current_app.config['REDIS_TTL_CAP'])
        return jsonify({'animes' : resource, 'cursor' : newCursor, 'end' : end}), 200

    try:
        user: User = db.session.execute(select(User)
                                        .where((User.id == user_id) & (User.deleted.is_(False)))
                                        ).scalar_one_or_none()
        if not user:
            hset_with_ttl(RedisInterface, f'user:{user_id}', {'__NF__':-1}, current_app.config['REDIS_TTL_EPHEMERAL'])
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

    # Cache as a group
    cache_grouped_resource(RedisInterface, cacheKey, 'anime', {anime['id']:anime for anime in _animes}, current_app.config['REDIS_TTL_WEAK'], current_app.config['REDIS_TTL_STRONG'], newCursor, end)
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
