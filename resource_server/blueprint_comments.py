from flask import Blueprint, jsonify, g, request
from werkzeug import Response
from werkzeug.exceptions import Conflict, NotFound, BadRequest
from sqlalchemy import select, delete, Row
from sqlalchemy.exc import SQLAlchemyError
from resource_server.models import db, Comment, CommentReport, ForumAdmin, CommentVote, CommentReport, Post, User, ReportTags
from auxillary.utils import genericDBFetchException, rediserialize
from auxillary.decorators import enforce_json
from resource_server.resource_auxillary import resource_existence_cache_precheck, update_global_counter, hset_with_ttl
from resource_server.resource_decorators import token_required
from resource_server.external_extensions import RedisInterface
from resource_server.redis_config import RedisConfig
from typing import Any
from datetime import datetime

COMMENTS_BLUEPRINT: Blueprint = Blueprint('comments', 'comments')

@COMMENTS_BLUEPRINT.route("/", methods=['POST'])
@enforce_json
@token_required
def comment_on_post() -> tuple[Response, int]:
    try:
        parent_post_id: int = int(g.REQUEST_JSON.get('post_id'))
        if not parent_post_id: 
            raise BadRequest('Parent post ID required to create a comment')
        body: str = str(g.REQUEST_JSON.get('body'))
        if not body: 
            raise BadRequest("Comment body missing")
    except (TypeError, ValueError):
        raise BadRequest('To post a comment, please ensure that the post ID and a valid comment body is provided')
    
    post_cache_key: str = f'{Post.__tablename__}:{parent_post_id}'
    post_mapping: dict[str, Any] = resource_existence_cache_precheck(client=RedisInterface, identifier=parent_post_id, resource_name=Post.__tablename__, cache_key=post_cache_key)

    # Check if post is closed, if yes then reject
    if post_mapping.get('closed') == '1':   # Cached resources have been serialized and hence booleans are casted to 0/1 str
        raise Conflict('Post is closed')
    if not post_mapping:
        try:
            post: Post = db.session.execute(select(Post)
                                            .where((Post.id == parent_post_id) & (Post.deleted.is_(False) & (Post.rtbf_hidden.isnot(True))))
                                            ).scalar_one_or_none()
            if not post:
                hset_with_ttl(RedisInterface, post_cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
                raise NotFound(f'No post with ID {parent_post_id} exists')
            if post.closed:
                raise Conflict('Post is closed')
            post_mapping: dict[str, Any] = post.__json_like__()
        except SQLAlchemyError: genericDBFetchException()
    
    comment: Comment = Comment(authorID=g.DECODED_TOKEN['sid'], parentForum=post.forum_id, epoch=datetime.now(), body=body.strip(), parentPost=parent_post_id)

    # Increment global counters for this post, and commenting user's 'total_comments' columns
    update_global_counter(interface=RedisInterface, delta=1, database=db, table=Post.__tablename__, column='total_comments', identifier=parent_post_id)
    update_global_counter(interface=RedisInterface, delta=1, database=db, table=User.__tablename__, column='total_comments', identifier=g.DECODED_TOKEN['sid'])
    update_global_counter(interface=RedisInterface, delta=1, database=db, table=User.__tablename__, column='aura', identifier=g.DECODED_TOKEN['sid'])
    
    # Queue insertion of new comment
    RedisInterface.xadd('INSERTIONS', rediserialize(comment.__attrdict__()) | {'table' : Comment.__tablename__})
    return jsonify({'message' : 'comment added!', 'body' : body, 'author' : g.DECODED_TOKEN['sub'], 'time' : comment.time_created.isoformat()}), 202

@COMMENTS_BLUEPRINT.route('/<int:comment_id>', methods=['DELETE'])
@token_required
def delete_comment(comment_id: int) -> tuple[Response, int]:
    cache_key: str = f'{Comment.__tablename__}:{comment_id}'
    flag_key: str = f'delete:{cache_key}'
    lock_key: str = f'lock:{flag_key}'
    comment_mapping: dict[str, Any] = resource_existence_cache_precheck(client=RedisInterface, identifier=comment_id, resource_name=Comment.__tablename__, cache_key=cache_key, deletion_flag_key=flag_key)

    # Cache prechecks passed, attempt to set lock for this action
    lock = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock:
        # Failing to acquire lock means another worker is performing this same request, treat this request as a duplicate
        raise Conflict(f'A request for this action is currently enqueued')

    try:
        if not comment_mapping:
            # A comment can be deleted by either a forum admin or the author of the comment
            comment: Comment = db.session.execute(select(Comment)
                                                  .where((Comment.id == comment_id) & 
                                                         (Comment.deleted.is_(False)))   # Allow explicit deletion of RTBF hidden comments too
                                                    ).scalar_one_or_none()
            if not comment:
                RedisInterface.delete(lock_key)
                raise NotFound(f"No comment with ID {comment_id} found")

            comment_mapping: dict[str, Any] = comment.__json_like__()

        # Check permissions
        if int(comment_mapping.get('author_id')) != g.DECODED_TOKEN.get('sid'):
            # Check whether admin
            forumAdmin: ForumAdmin = db.session.execute(select(ForumAdmin)
                                                        .where((ForumAdmin.forum_id == int(comment_mapping.get('parent_forum'))) & 
                                                                (ForumAdmin.user_id == g.DECODED_TOKEN.get('sid')))
                                                        ).scalar_one_or_none()
            if not forumAdmin:
                RedisInterface.delete(lock_key)
                raise Unauthorized('You do not have the necessary permissions to delete this comment')
    except SQLAlchemyError: 
        RedisInterface.delete(lock_key)
        genericDBFetchException()
    except Exception as e:
        RedisInterface.delete(lock_key)
        raise e
    try:
        # Decrement global counters for this post, and the user's total comments
        update_global_counter(interface=RedisInterface, delta=-1, database=db, table=Post.__tablename__, column='total_comments', identifier=int(comment_mapping['parent_post']))
        update_global_counter(interface=RedisInterface, delta=-1, database=db, table=User.__tablename__, column='total_comments', identifier=g.DECODED_TOKEN['sid'])
        
        # Queue comment for soft deletion, write intent as deleiton, and write cache with NF Sentinel mapping
        with RedisInterface.pipeline(transaction=False) as pipe:
            pipe.set(flag_key, RedisConfig.RESOURCE_DELETION_PENDING_FLAG, ex=RedisConfig.TTL_STRONGEST)
            pipe.xadd('SOFT_DELETIONS', {'id' : comment_id, 'table' : Comment.__tablename__})
            pipe.hset(cache_key, mapping={RedisConfig.NF_SENTINEL_KEY : RedisConfig.NF_SENTINEL_VALUE})
            pipe.expire(cache_key, RedisConfig.TTL_EPHEMERAL)
            pipe.execute()
    finally:
        RedisInterface.delete(lock_key)
    return jsonify({'message' : 'Comment deleted', 'comment' : comment_mapping}), 202

@COMMENTS_BLUEPRINT.route("/<int:comment_id>/vote", methods=["POST"])
@token_required
def vote_comment(comment_id: int) -> tuple[Response, int]:
    try:
        vote: int = int(request.args['type'])
        if vote != 0 and vote != 1: raise BadRequest("Invalid vote value (Should be 0 (downvote) or 1 (upvote))")
    except KeyError: raise BadRequest("Vote type (upvote/downvote) not specified")
    except ValueError: raise BadRequest("Invalid vote value (Should be 0 (downvote) or 1 (upvote))")
    
    # Request valid at the surface level
    incoming_intent: str = RedisConfig.RESOURCE_CREATION_PENDING_FLAG if vote == 1 else RedisConfig.RESOURCE_CREATION_PENDING_ALT_FLAG  # Alt flag is special value for downvotes. We need this here because a downvote is still resource creation, but different than an upvote obviously
    delta: int = -1 if not vote else 1
    cache_key: str = f'{Comment.__tablename__}:{comment_id}'

    # Verify comment's existence first
    comment_mapping: dict[str, Any] = resource_existence_cache_precheck(client=RedisInterface, identifier=comment_id, resource_name=Comment.__tablename__, cache_key=cache_key, deletion_flag_key=f'delete:{cache_key}')
    # Verify that this vote is not duplicate
    flag_key: str = f'{CommentVote}:{g.DECODED_TOKEN["sid"]}:{comment_id}'
    lock_key: str = f'lock:{flag_key}'
    with RedisInterface.pipeline(transaction=False) as pipe:
        pipe.get(flag_key)
        pipe.get(lock_key)
        latest_intent, lock = pipe.execute()
    
    if lock or latest_intent == incoming_intent: # Race condition, or latest intent is same as the intent carried by this request. Reject
        raise Conflict(f'A request for this action is currently enqueued')
    
    # Consult DB in case of partial/no information being read from cache
    previous_vote: bool = True if latest_intent == RedisConfig.RESOURCE_CREATION_PENDING_FLAG else False if latest_intent == RedisConfig.RESOURCE_CREATION_PENDING_ALT_FLAG else None
    try:
        if not (comment_mapping or latest_intent):
            # Complete cache miss, read state from DB
            joined_result: Row = db.session.execute(select(Comment, CommentVote.vote)
                                                    .outerjoin(CommentVote, (CommentVote.voter_id == g.DECODED_TOKEN['sid']) & (CommentVote.comment_id == Comment.id))
                                                    .where((Comment.id == comment_id) &
                                                           (Comment.deleted.is_(False)) &
                                                           (Comment.rtbf_hidden.isnot(True)))
                                                    ).first()
            if joined_result:
                comment_mapping: dict[str, str|int] = joined_result[0].__json_like__()
                previous_vote = joined_result[1]
        elif not comment_mapping:
            comment: Comment = db.session.execute(select(Comment)
                                                  .where((Comment.id == comment_id) &
                                                         (Comment.deleted.is_(False)) &
                                                         (Comment.rtbf_hidden.isnot(True)))
                                                  ).scalar_one_or_none()
            if comment:
                comment_mapping: dict[str, Any] = comment.__json_like__()
        elif not latest_intent:
            previous_vote: bool = db.session.execute(select(CommentVote.vote)
                                                     .where((CommentVote.voter_id == g.DECODED_TOKEN['sid']) & (CommentVote.comment_id == comment_id))
                                                     ).scalar_one_or_none()
    except SQLAlchemyError: genericDBFetchException()
    if not comment_mapping:
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
        raise NotFound(f'No comment with ID {comment_id} exists')
    if ((previous_vote and incoming_intent == RedisConfig.RESOURCE_CREATION_PENDING_FLAG) or 
        (previous_vote is False and incoming_intent == RedisConfig.RESOURCE_CREATION_PENDING_ALT_FLAG)):
            raise Conflict(f'Comment already {"upvoted" if vote else "downvoted"}')

    if previous_vote is not None:
        # At this stage, if a previous vote exists then the current intent is the opposite
        delta += 1
        db.session.execute(delete(CommentVote)
                           .where((CommentVote.voter_id == g.DECODED_TOKEN['sid']) & (CommentVote.comment_id == comment_id)))
        db.session.commit()

    if not vote: delta*=-1  # Negative vote for downvote
    # All checks passed, set lock
    lock = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock:
        # Failing to acquire lock means another worker is performing this same request, treat this request as a duplicate
        raise Conflict(f'A request for this action is currently enqueued')
    try:
        # Update global counters for this post's score, and user's aura
        update_global_counter(interface=RedisInterface, delta=delta, database=db, table=Comment.__tablename__, column='score', identifier=comment_id)
        update_global_counter(interface=RedisInterface, delta=delta, database=db, table=User.__tablename__, column='aura', identifier=comment_mapping.get('author_id'))

        # Write intent as vote type and append insertion to stream
        with RedisInterface.pipeline(transaction=False) as pipe:
            pipe.xadd('WEAK_INSERTIONS', fields={"voter_id" : g.DECODED_TOKEN['sid'], "comment_id" : comment_id, 'vote' : vote, 'table' : CommentVote.__tablename__})
            pipe.set(flag_key, incoming_intent, ex=RedisConfig.TTL_STRONGEST)
            pipe.execute()
    finally:
        RedisInterface.delete(lock_key)
    
    return jsonify({"message" : "Vote casted!"}), 202

@COMMENTS_BLUEPRINT.route("/<int:comment_id>/unvote", methods=["DELETE"])
@token_required
def unvote_comment(comment_id: int) -> tuple[Response, int]:    
    cache_key: str = f'{Comment.__tablename__}:{comment_id}'
    # Verify comment's existence first
    comment_mapping: dict[str, Any] = resource_existence_cache_precheck(client=RedisInterface, identifier=comment_id, resource_name=Comment.__tablename__, cache_key=cache_key, deletion_flag_key=f'delete:{cache_key}')
    
    # Verify that this unvote request is not duplicate
    flag_key: str = f'{CommentVote}:{g.DECODED_TOKEN["sid"]}:{comment_id}'
    lock_key: str = f'lock:{flag_key}'
    with RedisInterface.pipeline(transaction=False) as pipe:
        pipe.get(flag_key)
        pipe.get(lock_key)
        latest_intent, lock = pipe.execute()
    
    if lock or latest_intent == RedisConfig.RESOURCE_DELETION_PENDING_FLAG: # Race condition, or latest intent is same as the intent carried by this request. Reject
        raise Conflict(f'A request for this action is currently enqueued')
    
    # Consult DB in case of partial/no information being read from cache
    previous_vote: bool = True if latest_intent == RedisConfig.RESOURCE_CREATION_PENDING_FLAG else False if latest_intent == RedisConfig.RESOURCE_CREATION_PENDING_ALT_FLAG else None
    try:
        if not (comment_mapping or latest_intent):
            # Complete cache miss, read state from DB
            joined_result: Row = db.session.execute(select(Comment, CommentVote.vote)
                                                    .outerjoin(CommentVote, (CommentVote.voter_id == g.DECODED_TOKEN['sid']) & (CommentVote.comment_id == Comment.id))
                                                    .where((Comment.id == comment_id) &
                                                           (Comment.deleted.is_(False)) &
                                                           (Comment.rtbf_hidden.isnot(True)))
                                                    ).first()
            if joined_result:
                comment_mapping: dict[str, str|int] = joined_result[0].__json_like__()
                previous_vote = joined_result[1]
        elif not comment_mapping:
            comment: Comment = db.session.execute(select(Comment)
                                                  .where((Comment.id == comment_id) &
                                                         (Comment.deleted.is_(False)) &
                                                         (Comment.rtbf_hidden.isnot(True)))
                                                  ).scalar_one_or_none()
            if comment:
                comment_mapping: dict[str, Any] = comment.__json_like__()
        elif not latest_intent:
            previous_vote: bool = db.session.execute(select(CommentVote.vote)
                                                     .where((CommentVote.voter_id == g.DECODED_TOKEN['sid']) & (CommentVote.comment_id == comment_id))
                                                     ).scalar_one_or_none()
    except SQLAlchemyError: genericDBFetchException()
    if not comment_mapping:
        hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
        raise NotFound(f'No comment with ID {comment_id} exists')
    if previous_vote is None:
        raise Conflict(f'No vote casted on this comment (ID: {comment_id})')
    delta: int = -1 if previous_vote else 1

    # All checks passed, set lock
    lock = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock:
        # Failing to acquire lock means another worker is performing this same request, treat this request as a duplicate
        raise Conflict(f'A request for this action is currently enqueued')
    try:
        # Update global counters for this post's score, and user's aura
        update_global_counter(interface=RedisInterface, delta=delta, database=db, table=Comment.__tablename__, column='score', identifier=comment_id)
        update_global_counter(interface=RedisInterface, delta=delta, database=db, table=User.__tablename__, column='aura', identifier=comment_mapping.get('author_id'))

        # Write intent as vote type and append insertion to stream
        with RedisInterface.pipeline(transaction=False) as pipe:
            pipe.xadd('WEAK_DELETIONS', fields={"voter_id" : g.DECODED_TOKEN['sid'], "comment_id" : comment_id, 'table' : CommentVote.__tablename__})
            pipe.set(flag_key, RedisConfig.RESOURCE_DELETION_PENDING_FLAG, ex=RedisConfig.TTL_STRONGEST)
            pipe.execute()
    finally:
        RedisInterface.delete(lock_key)
    
    return jsonify({"message" : "Vote removed!"}), 202

@COMMENTS_BLUEPRINT.route("/<int:comment_id>/report", methods=['POST'])
@token_required
@enforce_json
def report_comment(comment_id: int) -> tuple[Response, int]:
    # NOTE: A user can report a comment only once for a given reason (based on ReportTag enum). Because of this, the locking+intent logic here would include the report tag as well    
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
        raise BadRequest("Malformatted report request")
    
    # Incoming request valid at face value, now check Redis for state
    cache_key: str = f'{Comment.__tablename__}:{comment_id}'
    flag_key: str = f'{CommentReport.__tablename__}:{g.DECODED_TOKEN["sid"]}:{report_tag}'    # Reserved for this report tag only, existence would hence imply duplication
    lock_key: str = f'lock:{flag_key}'

    # Verify comment existence through cache
    comment_mapping: dict[str, Any] = resource_existence_cache_precheck(client=RedisInterface, identifier=comment_id, resource_name=Comment.__tablename__, cache_key=cache_key, deletion_flag_key=f'delete:{cache_key}')
    if RedisInterface.get(flag_key):
        raise Conflict('You have already reported this post')

    # Attempt to set lock for this action
    lock = RedisInterface.set(lock_key, 1, ex=RedisConfig.TTL_STRONG, nx=True)
    if not lock:
        # Failing to acquire lock means another worker is performing this same request, treat this request as a duplicate
        raise Conflict(f'A request for this action is currently enqueued')
    
    prior_report: CommentReport = None 
    try:
        if not comment_mapping:
            # Comment existence unknown
            joined_result: Row = db.session.execute(select(Comment, CommentReport)
                                                    .outerjoin(CommentReport, (CommentReport.user_id == g.DECODED_TOKEN['sid']) & (CommentReport.comment_id == comment_id))
                                                    .where(Comment.id == comment_id)
                                                    ).first()
            if joined_result:
                comment_mapping: dict[str, Any] = joined_result[0].__json_like__()
                prior_report: CommentReport = joined_result[1]
        else:
            prior_report: CommentReport = db.session.execute(select(CommentReport)
                                                            .where((CommentReport.user_id == g.DECODED_TOKEN['sid']) & (CommentReport.comment_id == comment_id))
                                                            ).scalar_one_or_none()
    except SQLAlchemyError: 
        RedisInterface.delete(lock_key)
        genericDBFetchException()
    except Exception as e:
        RedisInterface.delete(lock_key)
        raise e
    if not comment_mapping:     # Both cache and DB failed to indicate that comment exists
        with RedisInterface.pipeline(transaction=False) as pipe:
            pipe.delete(lock_key)
            pipe.hset(cache_key, mapping={RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE})
            pipe.expire(cache_key, RedisConfig.TTL_EPHEMERAL)
            pipe.execute()
        raise NotFound(f'No comment with ID {comment_id} exists')
    if prior_report:     # Post already reported for this reason
        RedisInterface.delete(lock_key)
        conflict: Conflict = Conflict('You have already reported this post for this reason')
        conflict.kwargs = {'report' : prior_report.__json_like__()}
        raise conflict
    
    # All validations passed, report comment and write state for this action
    epoch_string: str = datetime.now().isoformat()
    report_mapping: dict[str, str|int] = {'user_id' : g.DECODED_TOKEN['sid'], "comment_id" : comment_id, 'report_tag' : report_tag, 'report_time' : epoch_string, 'report_description' : report_desc, 'table' : CommentReport.__tablename__}

    try:
        # Incremenet global counter for reports on this comment and insert record into comment_reports
        update_global_counter(interface=RedisInterface, delta=1, database=db, table=Post.__tablename__, column='reports', identifier=int(comment_mapping['parent_post']))
        # Write intent as creation for this report, and append weak insertion entry to stream
        with RedisInterface.pipeline(transaction=False) as pipe:
            pipe.set(flag_key, RedisConfig.RESOURCE_CREATION_PENDING_FLAG, ex=RedisConfig.TTL_STRONGEST)
            pipe.xadd('WEAK_INSERTIONS', fields=report_mapping)
            pipe.execute()
    finally:
        RedisInterface.delete(lock_key)
        
    return jsonify({"message" : "Comment reported!", 'report' : report_mapping}), 202
