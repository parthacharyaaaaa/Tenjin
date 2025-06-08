from flask import Blueprint, jsonify, g, url_for, request
from werkzeug import Response
from werkzeug.exceptions import NotFound, BadRequest, Forbidden, Conflict
from sqlalchemy import select, update, delete, Row
from sqlalchemy.exc import SQLAlchemyError
from resource_server.models import db, Post, User, Forum, ForumAdmin, PostSave, PostReport, PostVote, Comment, ReportTags
from resource_server.resource_decorators import pass_user_details, token_required
from resource_server.external_extensions import RedisInterface
from resource_server.resource_auxillary import update_global_counter, fetch_global_counters, cache_layer_integrity_check, write_action_state
from resource_server.redis_config import RedisConfig
from auxillary.decorators import enforce_json
from auxillary.utils import rediserialize, genericDBFetchException, consult_cache
from typing import Any, Optional
from redis.exceptions import RedisError
from resource_server.external_extensions import hset_with_ttl
import base64
import binascii
from datetime import datetime

post = Blueprint("post", "post", url_prefix="/posts")

@post.route("/", methods=["POST", ])
@enforce_json
@token_required
def create_post() -> tuple[Response, int]:
    if not (g.REQUEST_JSON.get('forum') and 
            g.REQUEST_JSON.get('title') and
            g.REQUEST_JSON.get('body')):
        raise BadRequest("Invalid details for creating a post")

    try:
        forumID: int = int(g.REQUEST_JSON['forum'])
        title: str = g.REQUEST_JSON['title'].strip()
        body: str = g.REQUEST_JSON['body'].strip()

    except (KeyError, ValueError):
        raise BadRequest("Malformatted post details")
    
    # Ensure author and forum actually exist
    author: User = db.session.execute(select(User)
                                      .where((User.id == g.DECODED_TOKEN['sid']) & (User.deleted.isnot(True)))
                                      ).scalar_one_or_none()
    if not author:
        nf: NotFound = NotFound("Invalid author ID")
        nf.__setattr__("kwargs", {"help" : "If you believe that this is an erorr, please contact support",
                                "_links" : {"login" : {"href" : url_for(".")}}})  #TODO: Replace this with an actual user support endpoint
    forum: Forum = db.session.execute(select(Forum).where(Forum.id == forumID)).scalar_one_or_none()
    if not forum:
        raise NotFound("This forum could not be found")
    
    additional_kw = {}
        
    # Push to INSERTION stream. Since the consumers of this stream expect the entire table data to be given, we can use our class definitions
    post: Post = Post(author.id, forum.id, title, body, datetime.now())
    RedisInterface.xadd("INSERTIONS", rediserialize(post.__attrdict__()) | {'table' : Post.__tablename__})

    # Write through can't be done here, since insertions is async. Incrementing the sequence would defeat the purpose of the stream anyways, so yeah :/
    update_global_counter(interface=RedisInterface, delta=1, database=db, table=Forum.__tablename__, column='posts', identifier=forumID)    
    update_global_counter(interface=RedisInterface, delta=1, database=db, table=User.__tablename__, column='total_posts', identifier=g.DECODED_TOKEN['sid'])    

    return jsonify({"message" : "post created", "info" : "It may take some time for your post to be visibile to others, keep patience >:3"}), 202

@post.route("/<int:post_id>", methods=["GET"])
@pass_user_details
def get_post(post_id : int) -> tuple[Response, int]:
    cacheKey: str = f'{Post._tablename__}:{post_id}'
    fetch_relation: bool = 'fetch_relation' in request.args and g.REQUESTING_USER
    post_mapping: Optional[dict[str, Any]] = consult_cache(RedisInterface, cacheKey, RedisConfig.TTL_CAP, RedisConfig.TTL_PROMOTION,RedisConfig.TTL_EPHEMERAL)
    
    # Fetch global counters if in memory
    global_score, global_comments, global_saves = fetch_global_counters(RedisInterface, f'post:{post_id}:score', f'post:{post_id}:total_comments', f'post:{post_id}:saves')

    if post_mapping:
        if RedisConfig.NF_SENTINEL_KEY in post_mapping:
            raise NotFound('No post with this ID could be found')
        
        # Update fetched mapping with global counters
        if global_score is not None:
            post_mapping['score'] = global_score
        if global_saves is not None:
            post_mapping['saves'] = global_saves
        if global_comments is not None:
            post_mapping['comments'] = global_comments

    else:
        # Cache miss
        try:
            resultSet: Row = db.session.execute(select(Post, User.deleted)
                                                   .join(Post, Post.author_id == User.id)
                                                   .where((Post.id == post_id) & (Post.deleted.is_(False)))
                                                   ).first()
            if not resultSet:
                hset_with_ttl(RedisInterface, cacheKey, {RedisConfig.NF_SENTINEL_KEY : RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
                raise NotFound(f"Post with id {post_id} could not be found :(")
            
            fetchedPost, anonymize = resultSet
            post_mapping = rediserialize(fetchedPost.__json_like__())
            if anonymize:
                post_mapping['author_id'] = None
            
            # Update fetched mapping with global counters
            if global_score is not None:
                post_mapping['score'] = global_score
            if global_saves is not None:
                post_mapping['saves'] = global_saves
            if global_comments is not None:
                post_mapping['comments'] = global_comments

            # Cache with updated counters
            hset_with_ttl(RedisInterface, cacheKey, post_mapping, RedisConfig.TTL_STRONG)

        except SQLAlchemyError: genericDBFetchException()

    # post_mapping has been fetched, either from cache or DB. Start constructing final JSON response
    res: dict[str, dict] = {'post' : post_mapping}

    if fetch_relation:
        # Check for user's relationships to the post as well
        postSaved, postVoted = False, None

        if not anonymize:
            # Check if user is owner of this post
            res['owner'] = g.REQUESTING_USER.get('sid') == post_mapping['author_id']

        # Check if saved, voted
        # 1: Consult cache
        with RedisInterface.pipeline(transaction=False) as pp:  # Get operation need not be constrained by atomicity
            pp.get(f'saves:{post_id}:{g.REQUESTING_USER["sid"]}')
            pp.get(f'votes:{post_id}:{g.REQUESTING_USER["sid"]}')
            pipeRes = pp.execute()
        
        postSaved, postVoted = pipeRes

        # 2: Fall back to db
        if not postSaved:
            postSaved = db.session.execute(select(PostSave)
                                           .where((PostSave.post_id == post_id) & (PostSave.user_id == g.REQUESTING_USER.get('sid')))
                                           ).scalar_one_or_none()
        if not postVoted:
            postVoted = db.session.execute(select(PostVote.vote)
                                           .where((PostVote.post_id == post_id) & (PostVote.voter_id == g.REQUESTING_USER.get('sid')))
                                           ).scalar_one_or_none()

        res['saved'] = bool(postSaved)
        res['voted'] = postVoted

        # No promotion logic for ephemral keys
        with RedisInterface.pipeline(transaction=False) as pp:  # Ephemeral set operation need not be constrained by atomicity
            pp.set(f'saves:{post_id}:{g.REQUESTING_USER["sid"]}', 1 if postSaved else 0, ex=RedisConfig.TTL_EPHEMERAL)
            pp.set(f'votes:{post_id}:{g.REQUESTING_USER["sid"]}', -1 if not postVoted else int(postVoted), ex=RedisConfig.TTL_EPHEMERAL)
            pp.execute()

    return jsonify(res), 200

@post.route("/<int:post_id>", methods=["PATCH"])
@enforce_json
@token_required
def edit_post(post_id : int) -> tuple[Response, int]:
    cache_key: str = f'{Post.__tablename__}:{post_id}'
    # Check for 404 sentinal value
    if RedisInterface.hget(cache_key, RedisConfig.NF_SENTINEL_KEY) == RedisConfig.NF_SENTINEL_VALUE:
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Reset ephemeral announcement
        raise NotFound(f'No post with ID {post_id} exists')
    
    # Ensure user is owner of this post
    owner: User = db.session.execute(select(User)
                                     .join(Post, Post.author_id == User.id)
                                     .where((Post.id == post_id) & (Post.deleted == False))
                                     ).scalar_one_or_none()
    if not owner:
        raise Forbidden('You do not have the rights to edit this post')
    
    if not (g.REQUEST_JSON.get('title') or 
            g.REQUEST_JSON.get('body') or 
            g.REQUEST_JSON.get('closed')):
        raise BadRequest("No changes sent")
    
    update_kw = {}
    additional_kw = {}
    if title := g.REQUEST_JSON.pop('title', None):
        title: str = title.strip()
        if title:
            update_kw['title'] = title
        else:
            additional_kw['title_err'] = "Invalid title"

    if body := g.REQUEST_JSON.pop('body', None):
        body: str = body.strip()
        if body:
            update_kw["body_text"] = body
        else:
            additional_kw["body_err"] = "Invalid body"

    if g.REQUEST_JSON.pop('closed', None):
        update_kw["closed"] = True
        
    if not update_kw:
        badReq = BadRequest("Empty request for updating post")
        badReq.__setattr__('kwargs', additional_kw)
        raise badReq

    updatedPost: Post = db.session.execute(update(Post)
                       .where(Post.id == post_id)
                       .values(**update_kw)
                       .returning(Post))
    db.session.commit()

    # Enforce write-through
    if RedisInterface.hgetall(cache_key):
        #NOTE: The hashmap for 404 ({RedisConfig.NF_SENTINEL_KEY : RedisConfig.NF_SENTINEL_VALUE}) would logically never be encountered since control reaching here indicates that the post obviously exists
        hset_with_ttl(RedisInterface, cache_key, updatedPost.__json_like__(), RedisConfig.TTL_STRONG)

    return jsonify({"message" : "Post edited. It may take a few seconds for the changes to be reflected",
                    "post_id" : post_id,
                    **update_kw, **additional_kw}), 202

@post.route("/<int:post_id>", methods=["DELETE"])
@token_required
def delete_post(post_id: int) -> Response:
    cache_key: str = f'{Post.__tablename__}:{post_id}'
    flag_key: str = f'delete:{cache_key}'           # User ID not included in intent flag, because multiple users (owner, admin) can delete a post
    lock_key: str = f'lock:{flag_key}'
    with RedisInterface.pipeline() as pipe:
        pipe.hgetall(cache_key)
        pipe.get(flag_key)
        pipe.get(lock_key)
        post_mapping, latest_intent, lock = pipe.execute()

    if post_mapping and RedisConfig.NF_SENTINEL_KEY in post_mapping:
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Reset ephemeral announcement
        raise NotFound(f'No post with ID {post_id} exists')
    if lock or latest_intent:
        raise Conflict(f'A request for this action is currently enqueued')
    
    # Ensure post exists in the first place
    if not post_mapping:
        try:
            post: Post = db.session.execute(select(Post)
                                            .where((Post.id == post_id) & (Post.deleted.is_(False)))
                                            ).scalar_one_or_none()
            if not post:
                hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY : RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
                raise NotFound('Post does not exist')
            else:
                post_mapping = post.__json_like__()
        except SQLAlchemyError: genericDBFetchException()
    
    # Ensure post author/forum admin is the issuer of this request
    if int(post_mapping['author_id']) != g.DECODED_TOKEN['sid']:
        # Check if admin of this forum
        forumAdmin: ForumAdmin = db.session.execute(select(ForumAdmin)
                                                    .where((ForumAdmin.forum_id == int(post_mapping['forum'])) & (ForumAdmin.user_id == g.DECODED_TOKEN['sid']))
                                                    ).scalar_one_or_none()
        if not forumAdmin:
            raise Forbidden('You do not have the rights to alter this post as you are not its author or an admin in its parent forum')

    # All checks passed, acquire lock and write intent
    lock_set = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock_set:
        raise Conflict('Failed to delete post, another request for deletion')
    
    RedisInterface.set(flag_key, RedisConfig.RESOURCE_CREATION_PENDING_FLAG, ex=RedisConfig.TTL_STRONGEST)  # Write intent as deletion
    try:
        # Decrement global counters
        update_global_counter(interface=RedisInterface, delta=-1, database=db, table=Forum.__tablename__, column='posts', identifier=post.forum_id)    
        update_global_counter(interface=RedisInterface, delta=-1, database=db, table=User.__tablename__, column='total_posts', identifier=g.DECODED_TOKEN['sid'])    
        # Post good to go for deletion
        RedisInterface.xadd("SOFT_DELETIONS", {'id' : post_id, 'table' : Post.__tablename__})
    finally:
        RedisInterface.delete(lock_key)
    
    redirectionForum: str = None
    if redirect := 'redirect' in request.args:
        try:
            redirectionForum = db.session.execute(select(Forum._name)
                                                  .where(Forum.id == int(post_mapping['forum']))
                                                  ).scalar_one()
        except SQLAlchemyError: ... # Fail silently, ain't no way we have done all that and then do a 500 because we didn't get a redirection link >:(

    # Overwrite any existing cached entries for this post with 404 mapping, and then expire ephemerally
    hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY : RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
    return jsonify({'message' : 'post deleted', 'redirect' : None if not redirect else url_for('templates.forum', _external = False, forum_name = redirectionForum)}), 202

@post.route("/<int:post_id>/vote", methods=["PATCH"])
@token_required
def vote_post(post_id: int) -> tuple[Response, int]:
    cache_key: str = f'{Post.__tablename__}:{post_id}'
    # Check for 404 sentinal value
    if RedisInterface.hget(cache_key, RedisConfig.NF_SENTINEL_KEY) == RedisConfig.NF_SENTINEL_VALUE:
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Reset ephemeral announcement
        raise NotFound(f'No post with ID {post_id} exists')
    
    try:
        vote: int = int(request.args['type'])
        if vote != 0 and vote != 1:
            raise ValueError
    except KeyError:
        raise BadRequest("Vote type (upvote/downvote) not specified")
    except ValueError:
        raise BadRequest("Invalid vote value (Should be 0 (downvote) or 1 (upvote))")
    
    # Request valid at the surface level, check for votes existence in db
    bSwitchVote: bool = False
    previous_vote: int = None
    vote_cache_key: str = f'votes:{post_id}:{g.DECODED_TOKEN["sid"]}'
    delta: int = 1

    # 1: Consult cache
    try:
        previous_vote = RedisInterface.get(vote_cache_key)
        if previous_vote is not None:
            previous_vote = int(previous_vote)
    except RedisError: ...
    
    # 2: Fall back to DB
    try:
        # Check if post exists AND if user has already casted a vote for this post
        _res: Row = db.session.execute(select(Post)
                                       .outerjoin(PostVote, (PostVote.post_id == post_id) & (PostVote.voter_id == g.DECODED_TOKEN['sid']))
                                       .where(Post.id == post_id)).first()
        if not _res:
            hset_with_ttl(RedisInterface, f'{Post.__tablename__}:{post_id}', {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
            raise NotFound(f'No post with ID {post_id} exists')
        
        previous_vote = _res[1]
        if previous_vote is not None:
            if int(previous_vote) == vote:
                # Casting the same vote twice, do nothing
                raise Conflict('Same vote cannot be cast twice on the same post')
            else:
                # Going from upvote to downvote, or vice-versa. Counter will be incremented or decremented by 2
                delta = 2
                # Remove old record for user VOTES ON posts in advance
                db.session.execute(delete(PostVote).where((PostVote.post_id == post_id) & (PostVote.voter_id == g.DECODED_TOKEN['sid'])))
                db.session.commit()
    except SQLAlchemyError: genericDBFetchException()

    if not vote: delta*=-1  # Negative vote for downvote

    update_global_counter(interface=RedisInterface, delta=delta, database=db, table=Post.__tablename__, column='score', identifier=post_id)
    RedisInterface.xadd("WEAK_INSERTIONS", {"voter_id" : g.DECODED_TOKEN['sid'], "post_id" : post_id, 'vote' : vote, 'table' : PostVote.__tablename__}) # Add post_votes record to insertion queue
    RedisInterface.set(vote_cache_key, vote, ex=RedisConfig.TTL_EPHEMERAL)    # Ephemerally cache that bad boy

    return jsonify({"message" : "Voted!"}), 202

@post.route("/<int:post_id>/unvote", methods=["PATCH"])
@token_required
def unvote_post(post_id: int) -> tuple[Response, int]:
    cache_key: str = f'{Post.__tablename__}:{post_id}'
    # Check for 404 sentinal value
    if RedisInterface.hget(cache_key, RedisConfig.NF_SENTINEL_KEY) == RedisConfig.NF_SENTINEL_VALUE:
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Reset ephemeral announcement
        raise NotFound(f'No post with ID {post_id} exists')
    
    try:
        # Check that post exists AND user has voted on this post
        _res: Row = db.session.execute(select(Post, PostVote)
                                       .outerjoin(PostVote, (PostVote.post_id == post_id) & (PostVote.voter_id == g.DECODED_TOKEN['sid']))
                                       .where(Post.id == post_id)).first()
        if not _res:
            hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
            raise NotFound(f'No post with ID {post_id} exists')
        
        if not _res[1]:
            raise Conflict(f'No vote casted for post with ID {post_id}')
    except SQLAlchemyError: genericDBFetchException()

    delta: int = -1 if PostVote.vote else 1
    
    update_global_counter(interface=RedisInterface, delta=delta, database=db, table=Post.__tablename__, column='score', identifier=post_id)    
    RedisInterface.xadd("WEAK_DELETIONS", {"voter_id" : g.DECODED_TOKEN['sid'], "post_id" : post_id, 'table' : PostVote.__tablename__})
    RedisInterface.delete(f'votes:{post_id}:{g.DECODED_TOKEN["sid"]}')    # Remove cached entry if exists

    return jsonify({"message" : "Removed vote!"}), 202

@post.route('/<int:post_id>/is-saved', methods=['GET'])
@pass_user_details
def check_post_saved(post_id: int) -> tuple[Response, int]:
    if not (g.REQUESTING_USER and g.REQUESTING_USER.get('sid')):
        return jsonify(False), 200
    
    cacheKey: str = f"saves:{post_id}:{g.REQUESTING_USER['sid']}"
    isSaved = RedisInterface.get(cacheKey)
    if isSaved:
        RedisInterface.set(cacheKey, int(isSaved), RedisConfig.TTL_EPHEMERAL)
        return jsonify(int(isSaved)), 200
    
    try:
        isSaved: int = int(bool(db.session.execute(select(PostSave)
                                                .where((PostSave.post_id == post_id) & (PostSave.user_id == g.REQUESTING_USER['sid']))
                                                ).scalar_one_or_none()))

        RedisInterface.set(cacheKey, isSaved, RedisConfig.TTL_EPHEMERAL)
        return jsonify(isSaved), 200
    
    except SQLAlchemyError:
        res = jsonify(False)
        res.headers['Error'] = 'An error occured when trying to check if you have saved this post'
        return res, 500

@post.route('/<int:post_id>/is-voted', methods=['GET'])
@pass_user_details
def check_post_vote(post_id: int) -> tuple[Response, int]:
    # Flags:
    # -1: Not voted
    #  0: Downvoted
    #  1: Upvoted

    if not (g.REQUESTING_USER and g.REQUESTING_USER.get('sid')):
        return jsonify(-1), 200
    
    cacheKey: str = f'votes:{post_id}:{g.REQUESTING_USER["sid"]}'
    postVote = RedisInterface.get(cacheKey)
    if postVote:
        RedisInterface.set(cacheKey, postVote, RedisConfig.TTL_EPHEMERAL)   # No promotion for an ephemeral key, just reset TTL
        return jsonify(int(postVote)), 200
    
    try:
        # Comment Of Shame: This line used to be select(PostVote.voter_id), massive skill issue
        postVote = db.session.execute(select(PostVote.vote)
                                      .where((PostVote.post_id == post_id) & (PostVote.voter_id == g.REQUESTING_USER['sid']))
                                      ).scalars().one_or_none()
        
        # 0 is falsey, hence 'not postVote' won't work as intended here
        if postVote == None:
            RedisInterface.set(cacheKey, -1, RedisConfig.TTL_EPHEMERAL)
            return jsonify(-1), 200
    except: genericDBFetchException()

    postVote = int(postVote)
    RedisInterface.set(cacheKey, postVote, RedisConfig.TTL_EPHEMERAL)
    return jsonify(postVote), 200

@post.route("/<int:post_id>/save", methods=["PATCH"])
@token_required
def save_post(post_id: int) -> tuple[Response, int]:
    cache_key: str = f'{Post.__tablename__}:{post_id}'
    flag_key: str = f'{PostSave.__tablename__}:{g.DECODED_TOKEN["sid"]}:{post_id}'
    lock_key: str = f'lock:{RedisConfig.RESOURCE_CREATION_PENDING_FLAG}:{flag_key}'    
    with RedisInterface.pipeline() as pipe:
        pipe.hgetall(cache_key)
        pipe.get(flag_key)
        pipe.get(lock_key)
        post_mapping, latest_intent, lock = pipe.execute()
    
    if post_mapping and RedisConfig.NF_SENTINEL_KEY in post_mapping:
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Reset ephemeral announcement
        raise NotFound(f'No post with ID {post_id} exists')
    if lock or latest_intent == RedisConfig.RESOURCE_CREATION_PENDING_FLAG:
        raise Conflict(f'A request for this action is currently enqueued')
    # Attempt to set lock for this action
    lock = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock:
        # Failed to acquire lock means another worker is performing this same request, treat this request as a duplicate
        raise Conflict(f'A request for this action is currently enqueued')
    
    # Lock acquired, perform all necessary valdiations
    post_exists: bool = bool(post_mapping)
    isSaved: bool = False if latest_intent == RedisConfig.RESOURCE_DELETION_PENDING_FLAG else True
    try:
        if not (post_mapping or latest_intent):
            # Nothing known at this point, query both Post and PostSave
            _res = db.session.execute(select(Post.id, PostSave)
                                             .outerjoin(PostSave, (PostSave.post_id == post_id) & (PostSave.user_id == g.DECODED_TOKEN['sid']))
                                             .where((Post.id == post_id) & (Post.deleted.is_(False)))).first()
            if _res:
                post_exists, isSaved = _res
        elif not latest_intent:
            # Fetch PostSave record to see if the user has already saved this post
            isSaved = bool(db.session.execute(select(PostSave)
                                              .where((PostSave.user_id == g.DECODED_TOKEN['sid']) & (PostSave.post_id == post_id))
                                              ).scalar_one_or_none())
            
        elif not post_mapping:
            post_exists = bool(db.session.execute(select(Post.id)
                                                  .where((Post.id == post_id) & (Post.deleted.is_(False)))).scalar_one_or_none())
    except SQLAlchemyError: 
        RedisInterface.delete(lock_key)
        genericDBFetchException()
    if not post_exists:
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Reset ephemeral announcement
        RedisInterface.delete(lock_key)
        raise NotFound(f'No post with ID {post_id} exists')
    # If post is already saved in database, then check if latest intent is to unsave. IF no, then this request is invalid
    if isSaved and latest_intent != RedisConfig.RESOURCE_DELETION_PENDING_FLAG:
        RedisInterface.delete(lock_key)
        raise Conflict('Post already saved')
    
    # All validations passed, change state for this action
    RedisInterface.set(flag_key, RedisConfig.RESOURCE_CREATION_PENDING_FLAG)    # Write/Update flag to show latest intent as post save
    
    # Only queue database operations if state was written succesfully
    try:
        update_global_counter(interface=RedisInterface, delta=1, database=db, table=Post.__tablename__, column='saves', identifier=post_id)    
        RedisInterface.xadd("WEAK_INSERTIONS", {"user_id" : g.DECODED_TOKEN['sid'], "post_id" : post_id, 'table' : PostSave.__tablename__})
    finally:
        RedisInterface.delete(lock_key)
    return jsonify({"message" : "Post saved!"}), 202

@post.route("/<int:post_id>/unsave", methods=["PATCH"])
@token_required
def unsave_post(post_id: int) -> tuple[Response, int]:
    cache_key: str = f'{Post.__tablename__}:{post_id}'
    flag_key: str = f'{PostSave.__tablename__}:{g.DECODED_TOKEN["sid"]}:{post_id}'
    lock_key: str = f'lock:{RedisConfig.RESOURCE_DELETION_PENDING_FLAG}:{flag_key}'    
    with RedisInterface.pipeline() as pipe:
        pipe.hgetall(cache_key)
        pipe.get(flag_key)
        pipe.get(lock_key)
        post_mapping, latest_intent, lock = pipe.execute()

    if post_mapping and RedisConfig.NF_SENTINEL_KEY in post_mapping:
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Reset ephemeral announcement
        raise NotFound(f'No post with ID {post_id} exists')
    
    if lock or latest_intent == RedisConfig.RESOURCE_DELETION_PENDING_FLAG:
        raise Conflict(f'A request for this action is currently enqueued')
    # Attempt to set lock for this action
    lock = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock:
        # Failed to acquire lock means another worker is performing this same request, treat this request as a duplicate
        raise Conflict(f'A request for this action is currently enqueued')
    
    post_exists: bool = bool(post_mapping)
    isSaved: bool = False if latest_intent == RedisConfig.RESOURCE_CREATION_PENDING_FLAG else True
    try:
        if not (post_mapping or latest_intent):
            # Nothing known at this point, query both Post and PostSave
            _res = db.session.execute(select(Post.id, PostSave)
                                             .outerjoin(PostSave, (PostSave.post_id == post_id) & (PostSave.user_id == g.DECODED_TOKEN['sid']))
                                             .where((Post.id == post_id) & (Post.deleted.is_(False)))).first()
            if _res:
                post_exists, isSaved = _res
        elif not latest_intent:
            # Fetch PostSave record to see if the user has already saved this post
            isSaved = bool(db.session.execute(select(PostSave)
                                              .where((PostSave.user_id == g.DECODED_TOKEN['sid']) & (PostSave.post_id == post_id))
                                              ).scalar_one_or_none())
            
        elif not post_mapping:
            post_exists = bool(db.session.execute(select(Post.id)
                                                  .where((Post.id == post_id) & (Post.deleted.is_(False)))
                                                  ).scalar_one_or_none())
    except SQLAlchemyError: 
        RedisInterface.delete(lock_key)
        genericDBFetchException()
    if not post_exists:
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Reset ephemeral announcement
        RedisInterface.delete(lock_key)
        raise NotFound(f'No post with ID {post_id} exists')
    # If post not saved in database, check to see if latest intent was to save. If not, reject this request
    if not (isSaved or latest_intent == RedisConfig.RESOURCE_CREATION_PENDING_FLAG):
        RedisInterface.delete(lock_key)
        raise Conflict('Post not saved in the first place')
    
    # All validations passed, change state for this action
    RedisInterface.set(flag_key, RedisConfig.RESOURCE_DELETION_PENDING_FLAG)    # Write/Update flag to show latest intent as post unsave
    
    try:
        update_global_counter(interface=RedisInterface, delta=-1, database=db, table=Post.__tablename__, column='saves', identifier=post_id)    
        RedisInterface.xadd("WEAK_DELETIONS", {"user_id" : g.DECODED_TOKEN['sid'], "post_id" : post_id, 'table' : PostSave.__tablename__})  # Queue post_saves record for deletion
    finally:
        RedisInterface.delete(lock_key)
    
    return jsonify({"message" : "Removed from saved posts"}), 202

@post.route("/<int:post_id>/report", methods=['POST'])
@token_required
@enforce_json
def report_post(post_id: int) -> tuple[Response, int]:
    # NOTE: A user can report a post only once for a given reason (based on ReportTag enum). Because of this, the locking+intent logic here would include the report tag as well    
    try:
        reportDescription: str = str(g.REQUEST_JSON.get('desc'))
        report_tag: str = str(g.REQUEST_JSON.get('tag'))
        if not (reportDescription and report_tag):
            raise BadRequest('Report request must contain description and a valid report reason')
        
        reportDescription = reportDescription.strip()
        report_tag = report_tag.strip().lower()

        if not ReportTags.check_membership(report_tag):
            raise BadRequest('Invalid report tag')
    except (ValueError, TypeError):
        raise BadRequest("Malformatted report request")
    
    # Incoming request valid at face value, now check Redis for state
    cache_key: str = f'{Post.__tablename__}:{post_id}'
    flag_key: str = f'{PostReport.__tablename__}:{g.DECODED_TOKEN["sid"]}:{post_id}:{report_tag}'    # It is important to include report tag here to differentiate between same reports with different tags
    lock_key: str = f'lock:{flag_key}'
    with RedisInterface.pipeline() as pipe:
        pipe.hgetall(cache_key)
        pipe.get(flag_key)
        pipe.get(lock_key)
        post_mapping, latest_intent, lock = pipe.execute()

    if post_mapping and RedisConfig.NF_SENTINEL_KEY in post_mapping:    # Post doesn't exist
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Reset ephemeral announcement
        raise NotFound(f'No post with ID {post_id} exists')
    
    if lock or latest_intent:   # Either another worker is performing the same action (Same user, reporting the same post for the same reason) or this action is already in queue for the same reason
        raise Conflict(f'A request for this action is currently enqueued')
    
    # Attempt to set lock for this action
    lock = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock:
        # Failing to acquire lock means another worker is performing this same request, treat this request as a duplicate
        raise Conflict(f'A request for this action is currently enqueued')
    
    post_exists: bool = bool(post_mapping)
    priorReport: PostReport = None
    try:
        if not (post_mapping or latest_intent):
            # Nothing known at this point, query both Post and PostReport
            _res = db.session.execute(select(Post.id, PostReport)
                                             .outerjoin(PostReport, (PostReport.user_id == g.DECODED_TOKEN['sid']) & (PostReport.post_id == post_id) & (PostReport.report_tag == report_tag))
                                             .where(Post.id == post_id)).first()
            if _res:
                post_exists, priorReport = _res
        elif not latest_intent:
            # Fetch PostRecord record to see if the user has already reported this post
            priorReport = db.session.execute(select(PostReport)
                                             .where((PostReport.user_id == g.DECODED_TOKEN['sid']) & (PostReport.post_id == post_id) & (PostReport.report_tag == report_tag))
                                             ).scalar_one_or_none()
            
        elif not post_mapping:
            post_exists = bool(db.session.execute(select(Post.id)
                                                  .where(Post.id == post_id)).scalar_one_or_none())
    except SQLAlchemyError: 
        RedisInterface.delete(lock_key)
        genericDBFetchException()
    
    if not post_exists:
        RedisInterface.delete(lock_key)
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Reset ephemeral announcement
        raise NotFound(f'No post with ID {post_id} exists')
    if priorReport:
        # Post already reported for this reason
        RedisInterface.delete(lock_key)
        raise Conflict('You have already reported this post for this reason')
    
    # All validations passed, write state for this action
    try:
        # Incremenet global counter for reports on this post and insert record into post_reports
        RedisInterface.set(flag_key, RedisConfig.RESOURCE_CREATION_PENDING_FLAG, ex=RedisConfig.TTL_STRONGEST)
        update_global_counter(interface=RedisInterface, delta=1, database=db, table=Post.__tablename__, column='reports', identifier=post_id)    
        RedisInterface.xadd("WEAK_INSERTIONS", {"user_id" : g.DECODED_TOKEN['sid'], "post_id" : post_id, 'report_tag' : report_tag, 'report_time' : datetime.now().isoformat(), 'report_description' : reportDescription, 'table' : PostReport.__tablename__})   # Queue insertion for post_reports
    finally:
        RedisInterface.delete(lock_key)
        
    return jsonify({"message" : "Post reported!"}), 202

@post.route("/<int:post_id>/comment", methods=['POST'])
@enforce_json
@token_required
def comment_on_post(post_id: int) -> tuple[Response, int]:
    commentBody = g.REQUEST_JSON.get('body')
    if not commentBody:
        raise BadRequest("Comment body missing")
    
    if not isinstance(commentBody, str):
        try:
            commentBody = str(commentBody)
        except:
            raise BadRequest('Invalid comment body')
            
    try:
        post: Post = db.session.execute(select(Post)
                                        .where(Post.id == post_id)
                                        ).scalar_one_or_none()
        if not post:
            raise NotFound('No such post exists')
    except SQLAlchemyError: genericDBFetchException()
    comment: Comment = Comment(g.DECODED_TOKEN['sid'], post.forum_id, datetime.now(), commentBody.strip(), post_id, None, None)

    # Increment global counters for this post, and commenting user's 'total_comments' columns
    update_global_counter(interface=RedisInterface, delta=1, database=db, table=Post.__tablename__, column='total_comments', identifier=post_id)
    update_global_counter(interface=RedisInterface, delta=1, database=db, table=User.__tablename__, column='total_comments', identifier=g.DECODED_TOKEN['sid'])
    
    # Queue insertion of new comment
    RedisInterface.xadd('INSERTIONS', rediserialize(comment.__attrdict__()) | {'table' : Comment.__tablename__})

    return jsonify({'message' : 'comment added!', 'body' : commentBody, 'author' : g.DECODED_TOKEN['sub']}), 202


@post.route('/<int:post_id>/comments')
def get_post_comments(post_id: int) -> tuple[Response, int]:
    try:
        rawCursor = request.args.get('cursor', '0').strip()
        if rawCursor == '0':
            cursor = 0
        else:
            cursor = int(base64.b64decode(rawCursor).decode())
    except (ValueError, TypeError, binascii.Error):
        raise BadRequest("Failed to load more posts. Please refresh this page")

    try:
        commentsDetails: list[tuple[str, Comment]] = db.session.execute(
            select(User, Comment)
            .where((Comment.id > cursor) & (Comment.parent_post == post_id))
            .join(User, Comment.author_id == User.id)
            .order_by(Comment.id)
            .limit(6)
        ).all()
    except SQLAlchemyError:
        return jsonify({'comments': None, 'cursor': cursor, 'end': True}), 200

    end: bool = False
    if len(commentsDetails) < 6:
        end = True
    else:
        commentsDetails.pop(-1)

    comments: list[dict] = []
    new_cursor = cursor

    for user, comment in commentsDetails:
        comments.append({
            'id': comment.id,
            'author': None if user.deleted else user.username,
            'body': comment.body,
            'created_at': comment.time_created.isoformat()
        })
        new_cursor = max(new_cursor, comment.id)

    encoded_cursor = base64.b64encode(str(new_cursor).encode()).decode()

    return jsonify({
        'comments': comments,
        'cursor': encoded_cursor,
        'end': end
    }), 200

@post.route('/<int:post_id>/comments/<int:comment_id>', methods=['DELETE'])
@token_required
def delete_comment(post_id: int, comment_id: int) -> tuple[Response, int]:
    cache_key: str = f'{Comment.__tablename__}:{comment_id}'
    flag_key: str = f'delete:{cache_key}'
    lock_key: str = f'lock:{flag_key}'
    with RedisInterface.pipeline() as pipe:
        pipe.hgetall(cache_key)
        pipe.get(flag_key)
        pipe.get(lock_key)
        comment_mapping, latest_intent, lock = pipe.execute()

    if comment_mapping and RedisConfig.NF_SENTINEL_KEY in comment_mapping:    # Comment doesn't exist
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Reset ephemeral announcement
        raise NotFound(f'No comment with ID {comment_id} exists. This may be because the parent post has been deleted')
    # NOTE: We are not checking for a post's existence, because a comment will be deleted upon the deletion of its parent post
    
    if lock or latest_intent:   # Either another worker is performing the same action (Same user, reporting the same post for the same reason) or this action is already in queue for the same reason
        raise Conflict(f'A request for this action is currently enqueued')
    
    try:
        if not comment_mapping:
            # A comment can be deleted by either a forum admin or the author of the comment
            joined_result: Row = db.session.execute(select(Comment, Post.deleted)
                                                    .join(Post, Post.id == Comment.parent_post)
                                                    .where((Comment.id == comment_id) & (Comment.deleted.is_(False)))
                                                    ).first()
            if not joined_result:
                raise NotFound(f"No comment with ID {comment_id} found")
            if joined_result[1]:
                raise NotFound(f'Comment with ID {comment_id} belongs to a deleted post, and was hence deleted')
            comment_mapping: dict[str, Any] = joined_result[0].__json_like__()
    except SQLAlchemyError: genericDBFetchException()

    if int(comment_mapping.get('author_id')) != g.DECODED_TOKEN.get('sid'):
        # Check whether admin
        forumAdmin: ForumAdmin = db.session.execute(select(ForumAdmin)
                                                    .where((ForumAdmin.forum_id == int(comment_mapping.get('parent_forum'))) & (ForumAdmin.user_id == g.DECODED_TOKEN.get('sid')))
                                                    ).scalar_one_or_none()
        if not forumAdmin:
            raise Unauthorized('You do not have the necessary permissions to delete this comment')
    # All checks passed, attempt to set lock for this action
    lock = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock:
        # Failing to acquire lock means another worker is performing this same request, treat this request as a duplicate
        raise Conflict(f'A request for this action is currently enqueued')
    
    try:
        RedisInterface.set(flag_key, RedisConfig.RESOURCE_DELETION_PENDING_FLAG, ex=RedisConfig.TTL_STRONGEST)  # Write flag for deleting this comment, NOTE: It's not required to have the pending value here, since intent is checked only by presence of this flag and not value. I've only used it to follow convention

        # Decrement global counters for this post, and the user's total comments
        update_global_counter(interface=RedisInterface, delta=-1, database=db, table=Post.__tablename__, column='total_comments', identifier=post_id)
        update_global_counter(interface=RedisInterface, delta=-1, database=db, table=User.__tablename__, column='total_comments', identifier=g.DECODED_TOKEN['sid'])
        
        # Queue comment for soft deletion
        RedisInterface.xadd('SOFT_DELETIONS', {'id' : comment_id, 'table' : Comment.__tablename__})
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY : RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL) # Announce deletion
    finally:
        RedisInterface.delete(lock_key)
    return jsonify({'message' : 'Comment deleted'}), 202
