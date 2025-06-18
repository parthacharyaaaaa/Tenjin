from flask import Blueprint, jsonify, g, url_for, request
from werkzeug import Response
from werkzeug.exceptions import NotFound, BadRequest, Forbidden, Conflict, Gone, InternalServerError
from sqlalchemy import select, update, delete, Row
from sqlalchemy.sql.expression import BinaryExpression
from sqlalchemy.exc import SQLAlchemyError
from resource_server.models import db, Post, User, Forum, ForumAdmin, PostSave, PostReport, PostVote, Comment, ReportTags
from resource_server.resource_decorators import pass_user_details, token_required
from resource_server.external_extensions import RedisInterface
from resource_server.resource_auxillary import update_global_counter, fetch_global_counters, posts_cache_precheck, resource_existence_cache_precheck, hset_with_ttl, resource_cache_precheck
from resource_server.redis_config import RedisConfig
from auxillary.decorators import enforce_json
from auxillary.utils import rediserialize, genericDBFetchException, consult_cache, fetch_group_resources, promote_group_ttl, cache_grouped_resource, to_base64url, from_base64url
from typing import Any, Optional, Sequence
import binascii
from datetime import datetime

POSTS_BLUEPRINT: Blueprint = Blueprint("post", "post")

@POSTS_BLUEPRINT.route("/", methods=["POST", ])
@enforce_json
@token_required
def create_post() -> tuple[Response, int]:
    if not (g.REQUEST_JSON.get('forum') and 
            g.REQUEST_JSON.get('title') and
            g.REQUEST_JSON.get('body')):
        raise BadRequest("Invalid details for creating a post. Please include: forum id, post title, and post body")

    try:
        forumID: int = int(g.REQUEST_JSON['forum'])
        title: str = g.REQUEST_JSON['title'].strip()
        body: str = g.REQUEST_JSON['body'].strip()

    except (KeyError, ValueError):
        raise BadRequest("Malformed post details. Title and body must be strings, and forum must be an integer/numeric string")
    
    # Ensure author and forum actually exist
    author: User = db.session.execute(select(User)
                                      .where((User.id == g.DECODED_TOKEN['sid']) & (User.deleted.isnot(True)))
                                      ).scalar_one_or_none()
    if not author:
        nf: NotFound = NotFound("Invalid author ID")
        nf.__setattr__("kwargs", {"help" : "If you believe that this is an erorr, please contact support",
                                "_links" : {"login" : {"href" : url_for("misc.issue_ticket")}}})
    forum: Forum = db.session.execute(select(Forum).where(Forum.id == forumID)).scalar_one_or_none()
    if not forum:
        raise NotFound("This forum could not be found") 
        
    # Push to INSERTION stream. Since the consumers of this stream expect the entire table data to be given, we can use our class definitions
    post: Post = Post(author.id, forum.id, title, body, datetime.now())
    RedisInterface.xadd("INSERTIONS", rediserialize(post.__attrdict__()) | {'table' : Post.__tablename__})

    # Write through can't be done here, since insertions is async. Incrementing the sequence would defeat the purpose of the stream anyways, so yeah :/
    update_global_counter(interface=RedisInterface, delta=1, database=db, table=Forum.__tablename__, column='posts', identifier=forumID)    
    update_global_counter(interface=RedisInterface, delta=1, database=db, table=User.__tablename__, column='total_posts', identifier=g.DECODED_TOKEN['sid'])    
    update_global_counter(interface=RedisInterface, delta=1, database=db, table=User.__tablename__, column='aura', identifier=g.DECODED_TOKEN['sid'])

    return jsonify({"message" : "post created", "info" : "It may take some time for your post to be visibile to others", 'time_posted' : post.time_posted.isoformat()}), 202

@POSTS_BLUEPRINT.route("/<int:post_id>", methods=["GET"])
@pass_user_details
def get_post(post_id : int) -> tuple[Response, int]:
    cacheKey: str = f'{Post.__tablename__}:{post_id}'
    fetch_relation: bool = 'fetch_relation' in request.args and g.REQUESTING_USER
    post_mapping: Optional[dict[str, Any]] = consult_cache(RedisInterface, cacheKey, RedisConfig.TTL_CAP, RedisConfig.TTL_PROMOTION,RedisConfig.TTL_EPHEMERAL)
    
    try:
        resultSet: Row = db.session.execute(select(Post, User.deleted)
                                            .join(Post, Post.author_id == User.id)
                                            .where((Post.id == post_id) & (Post.deleted.is_(False) & (Post.rtbf_hidden.isnot(True))))
                                            ).first()
        if not resultSet:
            hset_with_ttl(RedisInterface, cacheKey, {RedisConfig.NF_SENTINEL_KEY : RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
            raise NotFound(f"Post with id {post_id} could not be found :(")
        
        fetchedPost, anonymize = resultSet
        post_mapping = rediserialize(fetchedPost.__json_like__())
        if anonymize:
            post_mapping['author_id'] = None

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

@POSTS_BLUEPRINT.route("/<int:post_id>", methods=["PATCH"])
@enforce_json
@token_required
def edit_post(post_id : int) -> tuple[Response, int]:
    if not (g.REQUEST_JSON.get('title') or g.REQUEST_JSON.get('body') or g.REQUEST_JSON.get('closed')):
        raise BadRequest("No changes sent")
    
    cache_key: str = f'{Post.__tablename__}:{post_id}'
    post_mapping: dict[str, Any] = resource_existence_cache_precheck(client=RedisInterface, identifier=post_id, resource_name=Post.__tablename__, cache_key=cache_key, deletion_flag_key=f'delete:{cache_key}')
    
    if post_mapping and int(post_mapping['author_id']) != g.DECODED_TOKEN['sid']:
        raise Forbidden('You do not have the permission to edit this post as you are not its owner')
    
    # We'll need to hit DB once to verify that the requesting user actually owns this post. In case of a cache miss on post_id, we can do an outerjoin and limit calls to 1 always
    if not post_mapping:
        try:
            post: Post = db.session.execute(select(Post)
                                                    .where((Post.id == post_id) &
                                                           (Post.deleted.is_(False)) &
                                                           (Post.rtbf_hidden.isnot(True)))
                                                    ).scalar_one_or_none()
            if not post:
                hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
                raise NotFound(f'No post with id {post_id} exists')
            if post.author_id != g.DECODED_TOKEN['sid']:
                raise Forbidden('You do not have the rights to edit this post as you are not its author')
        except SQLAlchemyError: genericDBFetchException()

    update_kw: dict[str, str] = {}
    additional_kw: dict[str, str] = {}
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
    try:
        updatedPost: Post = db.session.execute(update(Post)
                                            .where(Post.id == post_id)
                                            .values(**update_kw)
                                            .returning(Post)
                                            ).scalar_one()
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        raise InternalServerError('An error occured when editing your post, please try again later')
    
    post_mapping = updatedPost.__json_like__()  # Update post_mapping with updated post
    
    # Enforce write-through
    hset_with_ttl(RedisInterface, cache_key, rediserialize(post_mapping), RedisConfig.TTL_WEAK)

    return jsonify({"message" : "Post edited.",
                    "post" : post_mapping}), 200

@POSTS_BLUEPRINT.route("/<int:post_id>", methods=["DELETE"])
@token_required
def delete_post(post_id: int) -> Response:
    cache_key: str = f'{Post.__tablename__}:{post_id}'
    flag_key: str = f'delete:{cache_key}'           # User ID not included in intent flag, because multiple users (owner, admin) can delete a post
    lock_key: str = f'lock:{flag_key}'
    # NOTE: Not using resource_existence_cache_precheck here since we also need to fetch lock for this action
    with RedisInterface.pipeline() as pipe:
        pipe.hgetall(cache_key)
        pipe.get(flag_key)
        pipe.get(lock_key)
        post_mapping, deletion_intent, lock = pipe.execute()

    if deletion_intent:
        raise Gone('This post has already been deleted')
    if post_mapping and RedisConfig.NF_SENTINEL_KEY in post_mapping:
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Reset ephemeral announcement
        raise NotFound(f'No post with ID {post_id} exists')
    if lock:
        raise Conflict(f'A request for this action is currently being processed')
    
    # Ensure post exists in the first place
    if not post_mapping:
        try:
            post: Post = db.session.execute(select(Post)
                                            .where((Post.id == post_id) & (Post.deleted.is_(False)))   # This endpoint still allows RTBF hidden posts to be explicitly deleted
                                            ).scalar_one_or_none()
            if not post:
                hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY : RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
                raise NotFound('Post does not exist')
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
    
    try:
        # Decrement global counters
        update_global_counter(interface=RedisInterface, delta=-1, database=db, table=Forum.__tablename__, column='posts', identifier=post.forum_id)    
        update_global_counter(interface=RedisInterface, delta=-1, database=db, table=User.__tablename__, column='total_posts', identifier=g.DECODED_TOKEN['sid'])    
        # Post good to go for deletion
        with RedisInterface.pipeline(transaction=False) as pipe:
            pipe.set(flag_key, RedisConfig.RESOURCE_DELETION_PENDING_FLAG, ex=RedisConfig.TTL_STRONGEST)
            pipe.xadd('SOFT_DELETIONS', fields={'id' : post_id, 'table' : Post.__tablename__})
            pipe.execute()
    finally:
        RedisInterface.delete(lock_key)
    
    redirectionForum: str = None
    if redirect := 'redirect' in request.args:
        try:
            redirectionForum: int = db.session.execute(select(Forum.id)
                                                  .where(Forum.id == int(post_mapping['forum']))
                                                  ).scalar_one()
        except SQLAlchemyError: ... # Fail silently, ain't no way we have done all that and then do a 500 because we didn't get a redirection link >:(

    # Overwrite any existing cached entries for this post with 404 mapping, and then expire ephemerally
    hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY : RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
    return jsonify({'message' : 'post deleted',
                     'redirect' : None if not redirect else url_for('FORUMS_BLUEPRINT.get_forum', _external = False, forum_id = redirectionForum),
                     'post' : post_mapping}), 202

@POSTS_BLUEPRINT.route("/<int:post_id>/votes", methods=["POST"])
@token_required
def vote_post(post_id: int) -> tuple[Response, int]:
    try:
        vote: int = int(request.args['type'])
        if vote != 0 and vote != 1: raise BadRequest("Invalid vote value (Should be 0 (downvote) or 1 (upvote))")
    except KeyError: raise BadRequest("Vote type (upvote/downvote) not specified")
    except ValueError: raise BadRequest("Invalid vote value (Should be 0 (downvote) or 1 (upvote))")
    
    # Request valid at the surface level
    incoming_intent: str = RedisConfig.RESOURCE_CREATION_PENDING_FLAG if vote == 1 else RedisConfig.RESOURCE_CREATION_PENDING_ALT_FLAG  # Alt flag is special value for downvotes. We need this here because a downvote is still resource creation, but different than an upvote obviously
    delta: int = 1
    cache_key: str = f'{Post.__tablename__}:{post_id}'
    flag_key: str = f'{PostVote.__tablename__}:{g.DECODED_TOKEN["sid"]}:{post_id}'
    lock_key: str = f'lock:{RedisConfig.RESOURCE_CREATION_PENDING_FLAG}:{flag_key}'

    post_mapping, latest_intent, queued_deletion = resource_cache_precheck(RedisInterface, post_id, cache_key, f'delete:{cache_key}', flag_key, lock_key)
    
    previous_vote: bool = True if latest_intent == RedisConfig.RESOURCE_CREATION_PENDING_FLAG else False if latest_intent == RedisConfig.RESOURCE_CREATION_PENDING_ALT_FLAG else None
    lock_set = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_EPHEMERAL, nx=True)
    if not lock_set: raise Conflict('Another worker is already performing this action')
    
    # Consult DB in case of partial/no information being read from cache
    try:
        if not (post_mapping or previous_vote):
            # Complete cache miss, read state from DB
            joined_result: Row = db.session.execute(select(Post, PostVote.vote)
                                                    .outerjoin(PostVote, (PostVote.post_id == post_id) & (PostVote.voter_id == g.DECODED_TOKEN['sid']))
                                                    .where((Post.id == post_id) & (Post.deleted.is_(False)) & (Post.rtbf_hidden.isnot(True)))
                                                    ).first()
            if joined_result:
                post_mapping: dict[str, str|int] = joined_result[0].__json_like__()
                previous_vote = joined_result[1]
        elif previous_vote is None:
            previous_vote = bool(db.session.execute(select(PostVote.vote)
                                                    .where((PostVote.voter_id == g.DECODED_TOKEN['sid']) & (PostVote.post_id == post_id))
                                                    ).scalar_one_or_none())
        elif not post_mapping:
            post: Post = db.session.execute(select(Post)
                                            .where((Post.id == post_id) & (Post.deleted.is_(False)) & (Post.rtbf_hidden.isnot(True)))
                                            ).scalar_one_or_none()
            if post: post_mapping = post.__json_like__()
    except SQLAlchemyError: 
        RedisInterface.delete(lock_key)
        genericDBFetchException()
    except Exception as e: 
        RedisInterface.delete(lock_key)
        raise e
    
    if not post_mapping:
        hset_with_ttl(RedisInterface, f'{Post.__tablename__}:{post_id}', {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
        RedisInterface.delete(lock_key)
        raise NotFound(f'No post with ID {post_id} exists')
    if ((previous_vote and incoming_intent == RedisConfig.RESOURCE_CREATION_PENDING_FLAG) or (previous_vote is False and incoming_intent == RedisConfig.RESOURCE_CREATION_PENDING_ALT_FLAG)):
        RedisInterface.delete(lock_key)
        raise Conflict(f'Post already {"upvoted" if vote else "downvoted"}')

    if previous_vote is not None:
        # At this stage, if a previous vote exists then the current intent is the opposite
        delta = 2
        try:
            db.session.execute(delete(PostVote)
                               .where((PostVote.voter_id == g.DECODED_TOKEN['sid']) & (PostVote.post_id == post_id)))
            db.session.commit()
        except SQLAlchemyError: 
            RedisInterface.delete(lock_key)
            db.session.rollback()
            raise InternalServerError('Failed to case vote on this post, please try again sometime later')
        except Exception as e: 
            RedisInterface.delete(lock_key)
            raise e

    if not vote: delta*=-1  # Negative vote for downvote
    try:
        # Update global counters for this post's score, and user's aura
        update_global_counter(interface=RedisInterface, delta=delta, database=db, table=Post.__tablename__, column='score', identifier=post_id)
        update_global_counter(interface=RedisInterface, delta=delta, database=db, table=User.__tablename__, column='aura', identifier=post_mapping.get('author_id'))
        # Write latest intent based on incoming vote and append query request to weak insertions stream
        with RedisInterface.pipeline() as pipe:
            pipe.set(flag_key, incoming_intent, ex=RedisConfig.TTL_STRONGEST)
            pipe.xadd('WEAK_INSERTIONS', {"voter_id" : g.DECODED_TOKEN['sid'], "post_id" : post_id, 'vote' : vote, 'table' : PostVote.__tablename__})
            pipe.execute()
    finally:
        RedisInterface.delete(lock_key)
    
    return jsonify({"message" : "Vote casted!"}), 202

@POSTS_BLUEPRINT.route("/<int:post_id>/votes", methods=["DELETE"])
@token_required
def unvote_post(post_id: int) -> tuple[Response, int]:
    # NOTE: A post pending deletion should still allow users to unvote
    cache_key: str = f'{Post.__tablename__}:{post_id}'
    flag_key: str = f'{PostVote.__tablename__}:{g.DECODED_TOKEN["sid"]}:{post_id}'
    lock_key: str = f'lock:{RedisConfig.RESOURCE_CREATION_PENDING_FLAG}:{flag_key}'

    post_mapping, latest_intent, queued_deletion = resource_cache_precheck(client=RedisInterface, identifier=post_id, cache_key=cache_key, deletion_intent_flag=f'delete:{cache_key}', action_flag=flag_key, lock_name=lock_key, allow_deletion=True)

    # Cache checks passed, set lock
    lock_set = RedisInterface.set(lock_key, value=1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock_set: raise Conflict(f'A request for this action is currently enqueued')

    delta: int = 1 if latest_intent == RedisConfig.RESOURCE_CREATION_PENDING_FLAG else -1 if latest_intent == RedisConfig.RESOURCE_CREATION_PENDING_ALT_FLAG else 0
    # Consult DB for partial/complete cache misses
    try:
        if not (post_mapping or latest_intent):
            # Complete cache miss, read state from DB
            joined_result: Row = db.session.execute(select(Post, PostVote.vote)
                                                    .outerjoin(PostVote, (PostVote.post_id == post_id) & (PostVote.voter_id == g.DECODED_TOKEN['sid']))
                                                    .where((Post.id == post_id) & (Post.deleted.is_(False)) & (Post.rtbf_hidden.isnot(True)))
                                                    ).first()
            if joined_result:
                post_mapping: dict[str, str|int] = joined_result[0].__json_like__()
                previous_vote = joined_result[1]
        elif not delta:
            previous_vote: bool = db.session.execute(select(PostVote.vote)
                                                     .where((PostVote.voter_id == g.DECODED_TOKEN['sid']) & (PostVote.post_id == post_id))
                                                     ).scalar_one_or_none()
            if previous_vote is not None:
                delta = -1 if previous_vote else 1
        elif not post_mapping:
            post: Post = db.session.execute(select(Post)
                                            .where((Post.id == post_id) & (Post.deleted.is_(False)) & (Post.rtbf_hidden.isnot(True)))
                                            ).scalar_one_or_none()
            if post:
                post_mapping = post.__json_like__()
    except SQLAlchemyError: 
        RedisInterface.delete(lock_key)
        genericDBFetchException()
    except Exception as e:
        RedisInterface.delete(lock_key)
        raise e
    if not post_mapping:
        RedisInterface.delete(lock_key)
        hset_with_ttl(RedisInterface, f'{Post.__tablename__}:{post_id}', {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
        raise NotFound(f'No post with ID {post_id} exists')
    if not delta:   # Neither cache nor DB was able to prove that the user has previously voted on this post, hence this request is invalid. Begone >:3
        RedisInterface.delete(lock_key)
        raise Conflict(f'Post not voted prior to unvote request')
    
    try:
        # Update counters for this post's score, and post author's aura
        update_global_counter(interface=RedisInterface, delta=delta, database=db, table=Post.__tablename__, column='score', identifier=post_id) 
        update_global_counter(interface=RedisInterface, delta=delta, database=db, table=User.__tablename__, column='aura', identifier=post_mapping.get('author_id')) 
        # Write intent as deletion, append deletion query request to weak deletions stream
        with RedisInterface.pipeline() as pipe:
            pipe.set(flag_key, RedisConfig.RESOURCE_DELETION_PENDING_FLAG, ex=RedisConfig.TTL_STRONGEST)
            pipe.xadd('WEAK_DELETIONS', {"voter_id" : g.DECODED_TOKEN['sid'], "post_id" : post_id, 'table' : PostVote.__tablename__})
            pipe.execute()
    finally:
        RedisInterface.delete(lock_key)
    
    response_body: dict = {"message" : "Removed vote!", 'previous_vote' : 'upvote' if delta == -1 else 'downvote'}
    if queued_deletion:
        response_body['deletion_notice'] = f"Post {post_id} will be deleted soon, and you may not be able to cast votes on this post henceforth" 
    return jsonify(response_body), 202

@POSTS_BLUEPRINT.route("/<int:post_id>/saves", methods=["POST"])
@token_required
def save_post(post_id: int) -> tuple[Response, int]:
    cache_key: str = f'{Post.__tablename__}:{post_id}'
    flag_key: str = f'{PostSave.__tablename__}:{g.DECODED_TOKEN["sid"]}:{post_id}'
    lock_key: str = f'lock:{RedisConfig.RESOURCE_CREATION_PENDING_FLAG}:{flag_key}'

    post_mapping, latest_intent, deletion_intent = resource_cache_precheck(RedisInterface, post_id, cache_key, f'delete:{cache_key}', action_flag=flag_key, lock_name=lock_key)
    # Cache precheck passed, set lock
    lock_set = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock_set:
            raise Conflict('Another worker is processing this exact request at the moment')
    # Lock acquired, perform all necessary valdiations
    isSaved: bool = False if latest_intent == RedisConfig.RESOURCE_DELETION_PENDING_FLAG else True
    try:
        if not (post_mapping or latest_intent):
            # Nothing known at this point, query both Post and PostSave
            joined_result: Row = db.session.execute(select(Post, PostSave)
                                                    .outerjoin(PostSave, (PostSave.post_id == post_id) & (PostSave.user_id == g.DECODED_TOKEN['sid']))
                                                    .where((Post.id == post_id) & (Post.deleted.is_(False)) & (Post.rtbf_hidden.isnot(True)))
                                                    ).first()
            if joined_result:
                post_mapping = joined_result[0].__json_like__()
                isSaved = bool(joined_result[1]) 
        elif not latest_intent:
            # Fetch PostSave record to see if the user has already saved this post
            isSaved = bool(db.session.execute(select(PostSave)
                                              .where((PostSave.user_id == g.DECODED_TOKEN['sid']) & (PostSave.post_id == post_id))
                                              ).scalar_one_or_none())
            
        elif not post_mapping:
            post: Post = db.session.execute(select(Post)
                                            .where((Post.id == post_id) & (Post.deleted.is_(False)) & (Post.rtbf_hidden.isnot(True)))
                                            ).scalar_one_or_none()
            if post: post_mapping = post.__json_like__()
    except SQLAlchemyError:
        RedisInterface.delete(lock_key)
        genericDBFetchException()
    except Exception as e:
        RedisInterface.delete(lock_key)
        raise e
    
    if not post_mapping:
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Reset ephemeral announcement
        RedisInterface.delete(lock_key)
        raise NotFound(f'No post with ID {post_id} exists')
    # If post is already saved in database, then check if latest intent is to unsave. IF no, then this request is invalid
    if isSaved and latest_intent != RedisConfig.RESOURCE_DELETION_PENDING_FLAG:
        RedisInterface.delete(lock_key)
        raise Conflict('Post already saved')

    # Only queue database operations if state was written succesfully
    try:
        update_global_counter(interface=RedisInterface, delta=1, database=db, table=Post.__tablename__, column='saves', identifier=post_id)   
        update_global_counter(interface=RedisInterface, delta=1, database=db, table=User.__tablename__, column='aura', identifier=g.DECODED_TOKEN['sid'])   
        # Write creation intent and append query data to weak insertions stream
        with RedisInterface.pipeline() as pipe:
            pipe.set(flag_key, RedisConfig.RESOURCE_CREATION_PENDING_FLAG, ex=RedisConfig.TTL_STRONGEST)
            pipe.xadd('WEAK_INSERTIONS', {"user_id" : g.DECODED_TOKEN['sid'], "post_id" : post_id, 'table' : PostSave.__tablename__})
            pipe.execute()
    finally:
        RedisInterface.delete(lock_key)
    return jsonify({"message" : "Post saved!"}), 202

@POSTS_BLUEPRINT.route("/<int:post_id>/saves", methods=["DELETE"])
@token_required
def unsave_post(post_id: int) -> tuple[Response, int]:
    cache_key: str = f'{Post.__tablename__}:{post_id}'
    flag_key: str = f'{PostSave.__tablename__}:{g.DECODED_TOKEN["sid"]}:{post_id}'
    lock_key: str = f'lock:{RedisConfig.RESOURCE_DELETION_PENDING_FLAG}:{flag_key}'    
    post_mapping, latest_intent, deletion_intent = resource_cache_precheck(RedisInterface, post_id, cache_key, f'delete:{cache_key}', action_flag=flag_key, lock_name=lock_key, allow_deletion=True)

    isSaved: bool = False if latest_intent == RedisConfig.RESOURCE_CREATION_PENDING_FLAG else True
    try:
        if not (post_mapping or latest_intent):
            # Nothing known at this point, query both Post and PostSave
            joined_result: Row = db.session.execute(select(Post, PostSave)
                                                    .outerjoin(PostSave, (PostSave.post_id == post_id) & (PostSave.user_id == g.DECODED_TOKEN['sid']))
                                                    .where((Post.id == post_id) & (Post.deleted.is_(False) & (Post.rtbf_hidden.isnot(True))))
                                                    ).first()
            if joined_result:
                post_mapping: dict[str, Any] = joined_result[0].__json_like__()
                isSaved = bool(joined_result[1])
        elif not latest_intent:
            # Fetch PostSave record to see if the user has already saved this post
            isSaved = bool(db.session.execute(select(PostSave)
                                              .where((PostSave.user_id == g.DECODED_TOKEN['sid']) & (PostSave.post_id == post_id))
                                              ).scalar_one_or_none())
            
        elif not post_mapping:
            post: Post = db.session.execute(select(Post)
                                            .where((Post.id == post_id) & (Post.deleted.is_(False) & (Post.rtbf_hidden.isnot(True))))
                                            ).scalar_one_or_none()
            if post: post_mapping = post.__json_like__()
    except SQLAlchemyError: 
        RedisInterface.delete(lock_key)
        genericDBFetchException()
    except Exception as e:
        RedisInterface.delete(lock_key)
        raise e
    if not post_mapping:
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)  # Reset ephemeral announcement
        RedisInterface.delete(lock_key)
        raise NotFound(f'No post with ID {post_id} exists')
    # If post not saved in database, check to see if latest intent was to save. If not, reject this request
    if not (isSaved or latest_intent == RedisConfig.RESOURCE_CREATION_PENDING_FLAG):
        RedisInterface.delete(lock_key)
        raise Conflict('Post not saved in the first place')
    
    # All validations passed, change state for this action    
    try:
        update_global_counter(interface=RedisInterface, delta=-1, database=db, table=Post.__tablename__, column='saves', identifier=post_id)
        # Write intent as deletion, and append deletion query to weak deletions stream
        with RedisInterface.pipeline() as pipe:
            pipe.set(flag_key, RedisConfig.RESOURCE_DELETION_PENDING_FLAG, ex=RedisConfig.TTL_STRONGEST)
            pipe.xadd('WEAK_DELETIONS', {"user_id" : g.DECODED_TOKEN['sid'], "post_id" : post_id, 'table' : PostSave.__tablename__})
            pipe.execute()
    finally:
        RedisInterface.delete(lock_key)
    
    response_body: dict[str, str] = {"message" : "Removed from saved posts"}
    if deletion_intent:
        response_body['deletion_notice'] = f"Post {post_id} will be deleted soon, and you may not be able to save this post henceforth" 
    return jsonify(response_body), 202

@POSTS_BLUEPRINT.route("/<int:post_id>/reports", methods=['POST'])
@token_required
@enforce_json
def report_post(post_id: int) -> tuple[Response, int]:
    # NOTE: A user can report a post only once for a given reason (based on ReportTag enum). Because of this, the locking+intent logic here would include the report tag as well    
    try:
        report_desc: str = str(g.REQUEST_JSON.get('desc'))
        report_tag: str = str(g.REQUEST_JSON.get('tag'))
        if not (report_desc and report_tag):
            raise BadRequest('Report request must contain description and a valid report reason')
        
        report_desc = report_desc.strip()
        report_tag = report_tag.strip().lower()

        if not ReportTags.check_membership(report_tag):
            raise BadRequest('Invalid report tag')
    except (ValueError, TypeError):
        raise BadRequest("Malformed report request")
    
    # Incoming request valid at face value, now check Redis for state
    cache_key: str = f'{Post.__tablename__}:{post_id}'
    flag_key: str = f'{PostReport.__tablename__}:{g.DECODED_TOKEN["sid"]}:{post_id}:{report_tag}'    # It is important to include report tag here to differentiate between same reports with different tags
    lock_key: str = f'lock:{flag_key}'

    post_mapping, latest_intent = posts_cache_precheck(client=RedisInterface, post_id=post_id, post_cache_key=cache_key, post_deletion_intent_flag=f'delete:{cache_key}', action_flag=flag_key, lock_name=lock_key)
    # Cache precheck passed, attempt to set lock for this action
    lock = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock:
        # Failing to acquire lock means another worker is performing this same request, treat this request as a duplicate
        raise Conflict(f'A request for this action is currently enqueued')
    
    priorReport: PostReport = None
    try:
        if not (post_mapping or latest_intent):
            # Nothing known at this point, query both Post and PostReport
            _res = db.session.execute(select(Post.id, PostReport)
                                             .outerjoin(PostReport, (PostReport.user_id == g.DECODED_TOKEN['sid']) & (PostReport.post_id == post_id) & (PostReport.report_tag == report_tag))
                                             .where((Post.id == post_id) & (Post.deleted.is_(False) & (Post.rtbf_hidden.isnot(True))))
                                             ).first()
            if _res:
                post_exists, priorReport = _res
        elif not latest_intent:
            # Fetch PostRecord record to see if the user has already reported this post
            priorReport = db.session.execute(select(PostReport)
                                             .where((PostReport.user_id == g.DECODED_TOKEN['sid']) & (PostReport.post_id == post_id) & (PostReport.report_tag == report_tag))
                                             ).scalar_one_or_none()
            
        elif not post_mapping:
            post: Post = db.session.execute(select(Post.id)
                                            .where((Post.id == post_id) & (Post.deleted.is_(False) & (Post.rtbf_hidden.isnot(True))))
                                            ).scalar_one_or_none()
            if post: post_mapping: dict[str, Any] = post.__json_like__()
    except SQLAlchemyError: 
        RedisInterface.delete(lock_key)
        genericDBFetchException()
    except Exception as e:
        RedisInterface.delete(lock_key)
        raise e
    
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
        update_global_counter(interface=RedisInterface, delta=1, database=db, table=Post.__tablename__, column='reports', identifier=post_id)
        with RedisInterface.pipeline() as pipe:
            pipe.set(flag_key, RedisConfig.RESOURCE_CREATION_PENDING_FLAG, ex=RedisConfig.TTL_STRONGEST)    # Flag value is irrelevant here since existence alone is used in cache prechecks, included only for consistency
            pipe.xadd('WEAK_INSERTIONS', {"user_id" : g.DECODED_TOKEN['sid'], "post_id" : post_id, 'report_tag' : report_tag, 'report_time' : datetime.now().isoformat(), 'report_description' : report_desc, 'table' : PostReport.__tablename__})
            pipe.execute()
    finally:
        RedisInterface.delete(lock_key)
    
    return jsonify({"message" : "Post reported!", 'reason' : report_tag, 'description' : report_desc}), 202

@POSTS_BLUEPRINT.route('/<int:post_id>/comments')
def get_post_comments(post_id: int) -> tuple[Response, int]:
    try:
        raw_cursor = request.args.get('cursor', '0').strip()
        if raw_cursor == '0':
            cursor: int = 0
        else:
            cursor: int = from_base64url(raw_cursor)
    except (ValueError, TypeError, binascii.Error):
        raise BadRequest("Failed to load more comments. Please try again later")

    cache_key: str = f'{Post.__tablename__}:{post_id}'
    pagination_cache_key: str = f'{cache_key}:{Comment.__tablename__}:{cursor}'
    post_mapping: dict[str, Any] = resource_existence_cache_precheck(client=RedisInterface, identifier=post_id, resource_name=User.__tablename__, cache_key=cache_key)
    if not post_mapping:
        # Ensure post exists before trying to fetch its comments
        try:
            post: Post = db.session.execute(select(Post)
                                            .where((Post.id == post_id) &
                                                   (Post.rtbf_hidden.isnot(True)) &
                                                   (Post.deleted.is_(False)))
                                            ).scalar_one_or_none()
            if not post:
                hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
                raise NotFound(f'No post with ID {post_id} could be found')
            
            post_mapping: dict[str, str|int] = rediserialize(post.__json_like__())
            hset_with_ttl(RedisInterface, cache_key, post_mapping, RedisConfig.TTL_WEAK)
        except SQLAlchemyError: genericDBFetchException()
    
    comments, end, next_cursor = fetch_group_resources(RedisInterface, group_key=pagination_cache_key)
    counter_attrs: list[str] = ['score']
    if comments and all(comments):
        counters_mapping: dict[str, Sequence[int|None]] = fetch_global_counters(client=RedisInterface, hashmaps=[f'{Comment.__tablename__}:{attr}' for attr in counter_attrs], identifiers=[comment['id'] for comment in comments])
        for idx, (attribute, counters) in enumerate(counters_mapping.items()):
            comments[idx][attribute] = counters[idx]
        
        # Return paginated result with updated counters
        promote_group_ttl(RedisInterface, group_key=pagination_cache_key, promotion_ttl=RedisConfig.TTL_PROMOTION, max_ttl=RedisConfig.TTL_CAP)
        return jsonify({'comments' : comments, 'cursor' : next_cursor, 'end' : end}), 200
    # Cache miss
    try:
        where_clause: BinaryExpression = (Comment.parent_post == post_id) & (Comment.deleted.is_(False)) & (Comment.rtbf_hidden.isnot(True))
        if cursor:
            where_clause &= (Comment.id > cursor)
        next_comments: list[tuple[Comment, str]] = db.session.execute(select(Comment, User.username)
                                                                      .join(User, User.id == Comment.author_id)
                                                                      .where(where_clause)
                                                                      .limit(10)
                                                                      .order_by(Comment.time_created.desc())
                                                                      ).all()
    except SQLAlchemyError: genericDBFetchException()
    if not next_comments:
        return jsonify({'comments' : None, 'end' : True, 'cursor' : raw_cursor})
    
    end: bool = len(next_comments) < 6
    if not end:
        next_comments.pop(-1)
    next_cursor: str = to_base64url(next_comments[-1][0].id, length=16)

    jsonified_comments: list[dict[str, Any]] = [comment_data[0].__json_like__() | {'username' : comment_data[1]} for comment_data in next_comments]
    # Cache grouped resources with updated counters
    cache_grouped_resource(RedisInterface, group_key=pagination_cache_key,
                           resource_type=Comment.__tablename__, resources={jsonified_comment['id'] : rediserialize(jsonified_comment) for jsonified_comment in jsonified_comments},
                           weak_ttl=RedisConfig.TTL_WEAK, strong_ttl=RedisConfig.TTL_STRONG,
                           cursor=next_cursor, end=end)

    return jsonify({'comments' : jsonified_comments, 'cursor' : next_cursor, 'end' : end}), 200
