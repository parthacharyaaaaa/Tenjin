from flask import Blueprint, Response, jsonify, g, url_for, request, current_app
post = Blueprint("post", "post", url_prefix="/posts")

from sqlalchemy import select, update, delete, Row
from sqlalchemy.exc import SQLAlchemyError

from resource_server.models import db, Post, User, Forum, ForumFlair, ForumAdmin, PostSave, PostVote, PostReport, Comment, ReportTags
from auxillary.decorators import enforce_json
from resource_server.resource_decorators import pass_user_details, token_required
from resource_server.external_extensions import RedisInterface
from auxillary.utils import rediserialize, genericDBFetchException, consult_cache

from werkzeug.exceptions import NotFound, BadRequest, Forbidden

from typing import Any, Optional
from redis.exceptions import RedisError
from resource_server.external_extensions import hset_with_ttl
import base64
import binascii
from datetime import datetime

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
        flair: str = g.REQUEST_JSON.get('flair', '').strip()

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
    if flair:
        # Ensure flair is valid for this forum
        validFlair = db.session.execute(select(ForumFlair).where((ForumFlair.forum_id == forum.id) & (ForumFlair.flair_name == flair))).scalar_one_or_none()
        if not validFlair:
            flair = None
            additional_kw["flair_err"] = f"Invalid flair for {forum._name}, defaulting to None."
        
    # Push to INSERTION stream. Since the consumers of this stream expect the entire table data to be given, we can use our class definitions
    post: Post = Post(author.id, forum.id, title, body, datetime.now(), flair)
    RedisInterface.xadd("INSERTIONS", rediserialize(post.__attrdict__()) | {'table' : Post.__tablename__})

    # Write through can't be done here, since insertions is async. Incrementing the sequence would defeat the purpose of the stream anyways, so yeah :/
    # Increment posts counter for this forum
    postsCounterKey: str = RedisInterface.hget(f"{Forum.__tablename__}:posts", forumID)
    if postsCounterKey:
        RedisInterface.incr(postsCounterKey)
        return jsonify({"message" : "post created", "info" : "It may take some time for your post to be visibile to others, keep patience >:3"}), 202

    # No counter, create one
    currentForumPosts: int = db.session.execute(select(Forum.posts).where(Forum.id == forumID)).scalar_one()
    postsCounterKey = f'forum:{forumID}:posts'
    op = RedisInterface.set(postsCounterKey, currentForumPosts+1, nx=True)
    if not op:
        # Counter made by another Flask worker
        RedisInterface.incr(postsCounterKey)
    else:
        RedisInterface.hset(f'{Forum.__tablename__}:posts', forumID, postsCounterKey)

    return jsonify({"message" : "post created", "info" : "It may take some time for your post to be visibile to others, keep patience >:3"}), 202

@post.route("/<int:post_id>", methods=["GET"])
@pass_user_details
def get_post(post_id : int) -> tuple[Response, int]:
    cacheKey: str = f'post:{post_id}'
    post_mapping: Optional[dict[str, Any]] = consult_cache(RedisInterface, cacheKey, current_app.config['REDIS_TTL_CAP'], current_app.config['REDIS_TTL_PROMOTION'],current_app.config['REDIS_TTL_EPHEMERAL'])

    if post_mapping:
        if '__NF__' in post_mapping:
            raise NotFound('No post with this ID could be found')

    else:
        try:
            resultSet: Row = db.session.execute(select(Post, User.deleted)
                                                   .join(Post, Post.author_id == User.id)
                                                   .where((Post.id == post_id) & (Post.deleted.is_(False)))
                                                   ).first()
            if not resultSet:
                hset_with_ttl(RedisInterface, cacheKey, {'__NF__' : -1}, current_app.config['REDIS_TTL_EPHEMERAL'])
                raise NotFound(f"Post with id {post_id} could not be found :(")
            
            fetchedPost, anonymize = resultSet
            post_mapping = rediserialize(fetchedPost.__json_like__())
            if anonymize:
                post_mapping['author_id'] = None
            hset_with_ttl(RedisInterface, cacheKey, post_mapping, current_app.config['REDIS_TTL_STRONG'])

        except SQLAlchemyError: genericDBFetchException()

    # post_mapping has been fetched, either from cache or DB. Start constructing final JSON response
    res: dict[str, dict] = {'post' : post_mapping}

    if g.REQUESTING_USER and 'fetch_relation' in request.args:
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
            pp.set(f'saves:{post_id}:{g.REQUESTING_USER["sid"]}', 1 if postSaved else 0, ex=current_app.config['REDIS_TTL_EPHEMERAL'])
            pp.set(f'votes:{post_id}:{g.REQUESTING_USER["sid"]}', -1 if not postVoted else int(postVoted), ex=current_app.config['REDIS_TTL_EPHEMERAL'])
            pp.execute()

    return jsonify(res), 200

@post.route("/<int:post_id>", methods=["PATCH"])
@enforce_json
@token_required
def edit_post(post_id : int) -> tuple[Response, int]:
    # Ensure user is owner of this post
    owner: User = db.session.execute(select(User)
                                     .join(Post, Post.author_id == User.id)
                                     .where((Post.id == post_id) & (Post.deleted == False))
                                     ).scalar_one_or_none()
    if not owner:
        raise Forbidden('You do not have the rights to edit this post')
    
    if not (g.REQUEST_JSON.get('title') or 
            g.REQUEST_JSON.get('body') or 
            g.REQUEST_JSON.get('flair') or 
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

    if flair := g.REQUEST_JSON.pop('flair', None):
        flair: str = flair.strip()
        if not flair:
            additional_kw['flair_err'] = "Flair cannot be empty/whitespace only"
        else:
            # Get forum ID
            forum: Forum = db.session.execute(select(Forum).join(Post, Post.forum_id == Forum.id).where(Post.id == post_id)).scalar_one_or_none()
            if not forum:
                raise NotFound("This forum does not exist")
            
            # Check if this flair exists for the queried Forum
            _flair: str = db.session.execute(select(ForumFlair).where((ForumFlair.forum_id == forum.id) & (ForumFlair.flair_name == flair))).scalar_one_or_none()
            if not _flair:
                flair = None
                additional_kw['flair_err'] = f'{forum._name} does not have any flair named {flair}'
            else:
                update_kw['flair'] = flair
        
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
    cacheKey: str = f'post:{post_id}'
    if RedisInterface.hgetall(cacheKey):
        #NOTE: The hashmap for 404 ({'__NF__' : '-1'}) would logically never be encountered since control reaching here indicates that the post obviously exists, hence we can directly check for empty maps
        hset_with_ttl(RedisInterface, cacheKey, updatedPost.__json_like__(), current_app.config['REDIS_TTL_STRONG'])

    return jsonify({"message" : "Post edited. It may take a few seconds for the changes to be reflected",
                    "post_id" : post_id,
                    **update_kw, **additional_kw}), 202

@post.route("/<int:post_id>", methods=["DELETE"])
@token_required
def delete_post(post_id: int) -> Response:
    redirect: bool = 'redirect' in request.args
    cacheKey: str = f'post:{post_id}'
    try:
        # Ensure post exists in the first place
        post: Post = db.session.execute(select(Post)
                                        .where(Post.id == post_id & (Post.deleted == False))
                                        ).scalar_one_or_none()
        if not post:
            hset_with_ttl(RedisInterface, cacheKey, {'__NF__' : -1}, current_app.config['REDIS_TTL_EPHEMERAL'])
            raise NotFound('Post does not exist')
        
        # Ensure post author is the issuer of this request
        if post.author_id != g.DECODED_TOKEN['sid']:
            # Check if admin of this forum
            forumAdmin: ForumAdmin = db.session.execute(select(ForumAdmin).where((ForumAdmin.forum_id == post.forum_id) & (ForumAdmin.user_id == g.DECODED_TOKEN['sid']))).scalar_one_or_none()
            if not forumAdmin:
                raise Forbidden('You do not have the rights to alter this post as you are not its author')

    except SQLAlchemyError: genericDBFetchException()

    # Post good to go for deletion
    RedisInterface.xadd("SOFT_DELETIONS", {'id' : post_id, 'table' : Post.__tablename__})
    
    if redirect:
        redirectionForum: str = db.session.execute(select(Forum._name).where(Forum.id == post.forum_id)).scalar_one()
    
    # Decrement posts counter for this forum
    postsCounterKey: str = RedisInterface.hget(f"{Forum.__tablename__}:posts", post.forum_id)
    if postsCounterKey:
        RedisInterface.decr(postsCounterKey)
        return jsonify({'message' : 'post deleted', 'redirect' : None if not redirect else url_for('templates.forum', _external = False, name = redirectionForum)}), 200

    # No counter, create one
    currentForumPosts: int = db.session.execute(select(Forum.posts).where(Forum.id == post.forum_id)).scalar_one()
    postsCounterKey = f'forum:{post.forum_id}:posts'
    op = RedisInterface.set(postsCounterKey, currentForumPosts, nx=True)
    if not op:
        # Counter made by another Flask worker
        RedisInterface.decr(postsCounterKey)
    else:
        RedisInterface.hset(f'{Forum.__tablename__}:posts', post.forum_id, postsCounterKey)
    
    # Overwrite any existing cached entries for this post with 404 mapping, and then expire ephemerally
    hset_with_ttl(RedisInterface, cacheKey, {'__NF__' : -1}, current_app.config['REDIS_TTL_EPHEMERAL'])
    return jsonify({'message' : 'post deleted', 'redirect' : None if not redirect else url_for('templates.forum', _external = False)}), 200

@post.route("/<int:post_id>/vote", methods=["PATCH"])
@token_required
def vote_post(post_id: int) -> tuple[Response, int]:
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
    postVote: int = None
    cacheKey: str = f'votes:{post_id}:{g.DECODED_TOKEN["sid"]}'

    # 1: Consult cache
    try:
        postVote = RedisInterface.get(cacheKey)
        if postVote:
            postVote = int(postVote)
    except RedisError: ...
    
    # 2: Fall back to DB
    try:
        # Check if user has already casted a vote for this post
        postVote: int = db.session.execute(select(PostVote.vote).where((PostVote.voter_id == g.DECODED_TOKEN['sid']) & (PostVote.post_id == post_id))).scalar()
        if postVote:
            if int(postVote) == vote:
                # Casting the same vote twice, do nothing
                return jsonify({'message' : f'Post already {"upvoted" if postVote else "downvoted"}'}), 200
            else:
                # Going from upvote to downvote, or vice-versa. Counter will be incremented or decremented by 2
                bSwitchVote = True
                RedisInterface.xadd("WEAK_DELETIONS", {'voter_id' : g.DECODED_TOKEN['sid'], 'post_id' : post_id, 'table' : PostVote.__tablename__})
    except SQLAlchemyError: genericDBFetchException()

    # Check if counter for this post's score exists already
    voteCounterKey: int = RedisInterface.hget(f"{Post.__tablename__}:score", post_id)
    if voteCounterKey:
        if vote:
            # Upvote
            RedisInterface.incr(voteCounterKey, 2 if bSwitchVote else 1) 
        else: 
            # Downvote
            RedisInterface.decr(voteCounterKey, 2 if bSwitchVote else 1) 

        # Add new weak entity for user VOTES ON posts
        RedisInterface.xadd("WEAK_INSERTIONS", {"voter_id" : g.DECODED_TOKEN['sid'], "post_id" : post_id, 'vote' : vote, 'table' : PostVote.__tablename__})
        RedisInterface.set(cacheKey, vote, ex=current_app.config['REDIS_TTL_EPHEMERAL'])    # Ephemerally cache that bad boy
        return jsonify({"message" : "Voted!"}), 202
    
    # No counter found
    try:
        # Fetch current score of post
        pScore: int = db.session.execute(select(Post.score).where(Post.id == post_id).with_for_update(nowait=True)).scalar_one_or_none()
        if pScore == None:
            raise NotFound("No such post exists")
    except SQLAlchemyError: genericDBFetchException()

    voteCounterKey: str = f'post:{post_id}:score'

    # Handle insertion and deletion of weak entities in advance
    if bSwitchVote:
        # Remove old WE for user VOTES ON posts in advance
        db.session.execute(delete(PostVote).where((PostVote.post_id == post_id) & (PostVote.voter_id == g.DECODED_TOKEN['sid'])))
        db.session.commit()

    # Insert new WE
    RedisInterface.xadd("WEAK_INSERTIONS", {"voter_id" : g.DECODED_TOKEN['sid'], "post_id" : post_id, 'vote' : vote, 'table' : PostVote.__tablename__})

    # Finally, update counter
    delta = 2 if bSwitchVote else 1
    op = RedisInterface.set(voteCounterKey, pScore+delta if vote else pScore-delta, nx=True)
    if not op:
        # Other Flask worker created a counter during handling of this request, update counter
        if vote:
            # Upvote
            RedisInterface.incr(voteCounterKey, 2 if bSwitchVote else 1) 
        else: 
            # Downvote
            RedisInterface.decr(voteCounterKey, 2 if bSwitchVote else 1)

        RedisInterface.set(cacheKey, vote, ex=current_app.config['REDIS_TTL_EPHEMERAL'])
        return jsonify({'message' : 'Voted!'}), 202

    RedisInterface.set(cacheKey, vote, ex=current_app.config['REDIS_TTL_EPHEMERAL'])

    # New counter set, add to this post's score hashmap and finish
    RedisInterface.hset(f"{Post.__tablename__}:score", post_id, voteCounterKey)
    return jsonify({"message" : "Voted!"}), 202

@post.route("/<int:post_id>/unvote", methods=["PATCH"])
@token_required
def unvote_post(post_id: int) -> tuple[Response, int]:    
    try:
        # Check if user has not casted a vote for this post, if yes then do nothing
        postVote: PostVote = db.session.execute(select(PostVote).where((PostVote.voter_id == g.DECODED_TOKEN['sid']) & (PostVote.post_id == post_id))).scalar_one_or_none()
        if not postVote:
            return jsonify({'message' : f'Post already voted'}), 200
    except SQLAlchemyError: genericDBFetchException()

    vote = PostVote.vote
    # Check if counter for this post's votes exists already
    voteCounterKey: str = RedisInterface.hget(f"{Post.__tablename__}:score", post_id)
    if voteCounterKey:
        if vote:
            # Remove upvote
            RedisInterface.decr(voteCounterKey)
        else: 
            # Remove downvote
            RedisInterface.incr(voteCounterKey)
        
        RedisInterface.xadd("WEAK_DELETIONS", {"voter_id" : g.DECODED_TOKEN['sid'], "post_id" : post_id, 'table' : PostVote.__tablename__})
        return jsonify({"message" : "Removed vote"}), 202
    
    # No counter found
    try:
        # Fetch current score of post
        pScore: int = db.session.execute(select(Post.score).where(Post.id == post_id)).scalar_one_or_none()
        if pScore == None:
            raise NotFound("No such post exists")
    except SQLAlchemyError: genericDBFetchException()

    voteCounterKey: str = f'post:{post_id}:score'

    # Delete WE in advance
    RedisInterface.xadd("WEAK_DELETIONS", {"voter_id" : g.DECODED_TOKEN['sid'], "post_id" : post_id, 'table' : PostVote.__tablename__})

    op = RedisInterface.set(voteCounterKey, pScore+1 if not vote else pScore-1, nx=True)
    if not op:
        # Other Flask worker created a counter during handling of this request, update counter
        if vote:
            # Remove upvote
            RedisInterface.decr(voteCounterKey)
        else: 
            # Remove downvote
            RedisInterface.incr(voteCounterKey)
        return jsonify({"message" : "Removed vote!"}), 202

    # New counter set, add it to this post's score hashmap and finish
    RedisInterface.hset(f"{Post.__tablename__}:score", post_id, voteCounterKey)
    return jsonify({"message" : "Removed vote!"}), 202

@post.route('/<int:post_id>/is-saved', methods=['GET'])
@pass_user_details
def check_post_saved(post_id: int) -> tuple[Response, int]:
    if not (g.REQUESTING_USER and g.REQUESTING_USER.get('sid')):
        return jsonify(False), 200
    
    cacheKey: str = f"saves:{post_id}:{g.REQUESTING_USER['sid']}"
    isSaved = RedisInterface.get(cacheKey)
    if isSaved:
        RedisInterface.set(cacheKey, int(isSaved), current_app.config['REDIS_TTL_EPHEMERAL'])
        return jsonify(int(isSaved)), 200
    
    try:
        isSaved: int = int(bool(db.session.execute(select(PostSave)
                                                .where((PostSave.post_id == post_id) & (PostSave.user_id == g.REQUESTING_USER['sid']))
                                                ).scalar_one_or_none()))

        RedisInterface.set(cacheKey, isSaved, current_app.config['REDIS_TTL_EPHEMERAL'])
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
        RedisInterface.set(cacheKey, postVote, current_app.config['REDIS_TTL_EPHEMERAL'])   # No promotion for an ephemeral key, just reset TTL
        return jsonify(int(postVote)), 200
    
    try:
        postVote = db.session.execute(select(PostVote.voter_id)
                                      .where((PostVote.post_id == post_id) & (PostVote.voter_id == g.REQUESTING_USER['sid']))
                                      ).scalars().one_or_none()
        
        # 0 is falsey, hence 'not postVote' won't work as intended here
        if postVote == None:
            RedisInterface.set(cacheKey, -1, current_app.config['REDIS_TTL_EPHEMERAL'])
            return jsonify(-1), 200
    except: genericDBFetchException()

    postVote = int(postVote)
    RedisInterface.set(cacheKey, postVote, current_app.config['REDIS_TTL_EPHEMERAL'])
    return jsonify(postVote), 200

@post.route("/<int:post_id>/save", methods=["PATCH"])
@token_required
def save_post(post_id: int) -> tuple[Response, int]:    
    try:
        # First check to see if the user has already saved this post. If yes, then do nothing
        savedPost: PostSave = db.session.execute(select(PostSave).where((PostSave.user_id == g.DECODED_TOKEN['sid']) & (PostSave.post_id == post_id))).scalar_one_or_none()
        if savedPost:
            return jsonify({'message' : 'post already saved'}), 200
    except SQLAlchemyError: genericDBFetchException()
    
    # If post counter exists, update directly
    saveCounterKey: int = RedisInterface.hget(f"{Post.__tablename__}:saves", post_id)
    if saveCounterKey:
        RedisInterface.incr(saveCounterKey)
        RedisInterface.xadd("WEAK_INSERTIONS", {"user_id" : g.DECODED_TOKEN['sid'], "post_id" : post_id, 'table' : PostSave.__tablename__})
        return jsonify({"message" : "Saved!"}), 202
        
    # Post counter does NOT exist
    try:
        # Fetch current post saves
        pSaves: int = db.session.execute(select(Post.saves).where(Post.id == post_id).with_for_update(nowait=True)).scalar_one_or_none()
        # 0 is falsy, so add extra check
        if pSaves == None:      
            raise NotFound("No such post exists")
    except SQLAlchemyError: genericDBFetchException()

    # Insert WE in advance
    RedisInterface.xadd("WEAK_INSERTIONS", {"user_id" : g.DECODED_TOKEN['sid'], "post_id" : post_id, 'table' : PostSave.__tablename__})  # Add post_saves entry to db

    # Create a new counter for this post
    saveCounterKey: str = f"post:{post_id}:saves"
    op = RedisInterface.set(saveCounterKey, pSaves+1, nx=True)
    if not op:
        # Other Flask worker already made a counter while this request was being processed, update counter and end
        RedisInterface.incr(saveCounterKey)
        return jsonify({"message" : "Saved!"}), 202

    RedisInterface.hset(f"{Post.__tablename__}:saves", post_id, saveCounterKey) # Add post's counter to saves hashmap for this table

    # Ephemerally set save flag
    RedisInterface.set(f"saves:{post_id}:{g.DECODED_TOKEN['sid']}", 1, current_app.config['REDIS_TTL_EPHEMERAL'])
    return jsonify({"message" : "Saved!"}), 202

@post.route("/<int:post_id>/unsave", methods=["PATCH"])
@token_required
def unsave_post(post_id: int) -> tuple[Response, int]:
    try:
        # First check to see if the user hasn't saved this post. If yes, then do nothing
        savedPost: PostSave = db.session.execute(select(PostSave).where((PostSave.user_id == g.DECODED_TOKEN['sid']) & (PostSave.post_id == post_id))).scalar_one_or_none()
        if not savedPost:
            return jsonify({'message' : 'post not saved'}), 200
    except SQLAlchemyError: genericDBFetchException()

    # If post counter exists, decrement directly
    saveCounterKey: int = RedisInterface.hget(f"{Post.__tablename__}:saves", post_id)
    if saveCounterKey:
        RedisInterface.decr(saveCounterKey)
        RedisInterface.xadd("WEAK_DELETIONS", {"user_id" : g.DECODED_TOKEN['sid'], "post_id" : post_id, 'table' : PostSave.__tablename__})
        return jsonify({"message" : "Removed from saved posts"}), 202
        
    # Post counter does NOT exist
    try:
        # Fetch current post saves
        pSaves: int = db.session.execute(select(Post.saves).where(Post.id == post_id).with_for_update(nowait=True)).scalar_one_or_none()
        # 0 is falsy, so add extra check
        if pSaves == None: 
            raise NotFound("No such post exists")
    except SQLAlchemyError: genericDBFetchException()

    # Add WE in advance
    RedisInterface.xadd("WEAK_DELETIONS", {"user_id" : g.DECODED_TOKEN['sid'], "post_id" : post_id, 'table' : PostSave.__tablename__})  # Add post_saves entry to db

    # Create a new counter for this post
    saveCounterKey: str = f"post:{post_id}:saves"
    op = RedisInterface.set(saveCounterKey, pSaves-1, nx=True)
    if not op:
        # Other Flask worker already made a counter while this request was being processed, decrement counter and end
        RedisInterface.decr(saveCounterKey)
        return jsonify({"message" : "Removed from saved posts"}), 202

    RedisInterface.hset(f"{Post.__tablename__}:saves", post_id, saveCounterKey) # Add post's counter to saves hashmap for this table

    # Ephemerally set save flag to False
    RedisInterface.set(f"saves:{post_id}:{g.DECODED_TOKEN['sid']}", 0, current_app.config['REDIS_TTL_EPHEMERAL'])
    return jsonify({"message" : "Removed from saved posts"}), 202

@post.route("/<int:post_id>/report", methods=[])
@token_required
@enforce_json
def report_post(post_id: int) -> tuple[Response, int]:
    try:
        reportDescription: str = str(g.REQUEST_JSON.get('desc'))
        reportTag: str = str(g.REQUEST_JSON.get('tag'))

        if not (reportDescription and reportTag):
            raise BadRequest('Report request must contain description and a valid report reason')
        
        reportDescription = reportDescription.strip()
        reportTag = reportTag.strip().lower()

        if not ReportTags.check_membership(reportTag):
            raise BadRequest('Invalid report tag')

    except (ValueError, TypeError) as e:
        raise BadRequest("Malformatted report request")
    
    # Check if user has reported this post already. If so, do nothing
    try:
        reportedPost: PostReport = db.session.execute(select(PostReport).where((PostReport.user_id == g.DECODED_TOKEN['sid']) & (PostReport.post_id == post_id))).scalar_one_or_none()
        if reportedPost:
            return jsonify({'message' : 'post reported already'}), 409
    except SQLAlchemyError: genericDBFetchException()

    # Request is for a new report on this post
    # Check to see if a counter for this post already exists
    reportCounterKey: int = RedisInterface.hget(f"{Post.__tablename__}:reports", post_id)

    # Exists
    if reportCounterKey:
        RedisInterface.xadd("WEAK_INSERTIONS", {"user_id" : g.DECODED_TOKEN['sid'], "post_id" : post_id, 'report_time' : datetime.now().isoformat(), 'report_description' : reportDescription, 'report_tag' : reportTag, 'table' : PostReport.__tablename__})
        RedisInterface.incr(reportCounterKey)
        return jsonify({"message" : "Reported!"}), 202
    
    # Does not exist
    pReports: int = db.session.execute(select(Post.reports).where(Post.id == post_id).with_for_update(nowait=True)).scalar_one_or_none()
    if pReports == None:
        raise NotFound("No such post exists")

    # Insert WE in advance
    RedisInterface.xadd("WEAK_INSERTIONS", {"user_id" : g.DECODED_TOKEN['sid'], "post_id" : post_id, 'report_time' : datetime.now().isoformat(), 'report_description' : reportDescription, 'report_tag' : reportTag, 'table' : PostReport.__tablename__})

    reportCounterKey: str = f'post:{post_id}:reports'
    op = RedisInterface.set(reportCounterKey, pReports+1, nx=True)
    if not op:
        # Other Flask worker already made a counter while this request was being processed, increment counter and end
        RedisInterface.incr(reportCounterKey)
        return jsonify({"message" : "Reported!"}), 202
    
    RedisInterface.hset(f"{Post.__tablename__}:reports", post_id, reportCounterKey)
    return jsonify({"message" : "Reported!"}), 202

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
        post: Post = db.session.execute(select(Post).where(Post.id == post_id)).scalar_one_or_none()
        if not post:
            raise NotFound('No such post exists')
    except SQLAlchemyError: genericDBFetchException()
    comment: Comment = Comment(g.DECODED_TOKEN['sid'], post.forum_id, datetime.now(), commentBody.strip(), post_id, None, None)

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
    cacheKey: str = f'comment:{comment_id}'
    if '__NF__' in RedisInterface.hgetall(cacheKey):
        hset_with_ttl(RedisInterface, cacheKey, {'__NF__':-1}, current_app.config['REDIS_TTL_EPHEMERAL'])
        return jsonify({'message' : 'This comment has already been deleted'})
    
    try:
        # A comment can be deleted by either a forum admin or the author of the comment
        comment: Comment = db.session.execute(select(Comment)
                                              .where((Comment.id == comment_id) & (Comment.deleted.isnot(True)))
                                              ).first()
        if not comment:
            raise NotFound("This comment could not be found")
    except SQLAlchemyError: genericDBFetchException()

    if not (comment.author_id == g.DECODED_TOKEN.get('sid')):
        # Check whether admin
        forumAdmin: ForumAdmin = db.session.execute(select(ForumAdmin)
                                                    .where((ForumAdmin.forum_id == comment.parent_forum) & (ForumAdmin.user_id == g.DECODED_TOKEN.get('sid')))
                                                    ).scalar_one_or_none()
        if not forumAdmin:
            raise Unauthorized('You do not have the necessary permissions to delete this comment')
    
    # Requestor has necessary permissions for comment deletion
    RedisInterface.xadd('SOFT_DELETIONS', {'id' : comment_id, 'table' : Comment.__tablename__})

    # Update comments counter for this post
    # Check to see if a counter for this post already exists
    commentCounterKey: str = RedisInterface.hget(f"{Post.__tablename__}:total_comments", post_id)

    response: Response = jsonify({'message' : 'Comment deleted'})
    # Exists
    if commentCounterKey:
        RedisInterface.decr(commentCounterKey)
        return response, 202
    
    # Does not exist
    pComments: int = db.session.execute(select(Post.total_comments)
                                        .where(Post.id == post_id)
                                        ).scalar_one_or_none()

    commentCounterKey: str = f'post:{post_id}:total_comments'
    op = RedisInterface.set(commentCounterKey, pComments+1, nx=True)
    if not op:
        # Other Flask worker already made a counter while this request was being processed, decrement counter and end
        RedisInterface.decr(commentCounterKey)
        return response, 202
    
    RedisInterface.hset(f"{Post.__tablename__}:reports", post_id, commentCounterKey)
    return response, 202