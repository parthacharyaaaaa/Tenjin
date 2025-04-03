from flask import Blueprint, Response, jsonify, g, url_for, request
post = Blueprint("post", "post", url_prefix="/posts")

from sqlalchemy import select, update
from resource_server.models import db, Post, User, Forum, forum_flairs
from uuid import uuid4
from auxillary.decorators import enforce_json, token_required
from resource_server.external_extensions import RedisInterface
from auxillary.utils import rediserialize

from werkzeug.exceptions import NotFound, BadRequest

from datetime import datetime

@post.route("/", methods=["POST", "OPTIONS"])
@enforce_json
@token_required
def create_post() -> Response:
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
    author: User = db.session.execute(select(User).where(User.id == g.decodedToken['sid'])).scalar_one_or_none()
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
        validFlair = db.session.execute(select(forum_flairs).where((forum_flairs.forum_id == forum.id) & (forum_flairs.flair_name == flair))).scalar_one_or_none()
        if not validFlair:
            flair = None
            additional_kw.update["flair_err"] = f"Invalid flair for {forum._name}, defaulting to None."
        
    # Push to INSERTION stream. Since the consumers of this stream expect the entire table data to be given, we can use our class definitions
    post: Post = Post(author.id, forum.id, title, body, datetime.now(), flair)
    RedisInterface.xadd("INSERTIONS", rediserialize(post.__attrdict__()) | {'table' : Post.__tablename__})

    return jsonify({"message" : "post created", "info" : "It may take some time for your post to be visibile to others, keep patience >:3"}), 202

@post.route("/<int:post_id>", methods=["GET", "OPTIONS"])
def get_post(post_id : int) -> Response:
    post : Post | None = db.session.execute(select(Post).where(Post.id == post_id)).scalar_one_or_none()
    if not post:
        raise NotFound(f"Post with id {post_id} could not be found :(")
    return jsonify(post.__json_like__()), 200

@post.route("/<int:post_id>", methods=["PATCH", "OPTIONS"])
@enforce_json
@token_required
def edit_post(post_id : int) -> tuple[Response, int]:
    if not (g.REQUEST_JSON.get('title') or 
            g.REQUEST_JSON.get('body') or 
            g.REQUEST_JSON.get('flair') or 
            g.REQUEST_JSON.get('closed')):
        raise BadRequest("No changes sent")
    
    update_kw = {}
    additional_kw = {}
    if g.REQUEST_JSON.get('title'):
        title: str = g.REQUEST_JSON.pop('title').strip()
        if title:
            update_kw['title'] = title
        else:
            additional_kw['title_err'] = "Invalid title"

    if g.REQUEST_JSON.get('body'):
        body: str = g.REQUEST_JSON.pop('body').strip()
        if body:
            update_kw["body"] = body
        else:
            additional_kw["body_err"] = "Invalid body"
    if g.REQUEST_JSON.get('closed'):
        g.REQUEST_JSON.pop('closed')
        update_kw["closed"] = True

    if not update_kw:
        raise BadRequest("Empty request for updating post")

    if g.REQUEST_JSON.get('flair'):
        flair: str = g.REQUEST_JSON.pop('flair').strip()
        if flair:
            # Again, check if flair is valid
            forumID: int = db.session.execute(select(Forum.id).join(Post, Post.forum_id == Forum.id).where(Post.id == post_id)).scalar_one_or_none()
            if not forumID:
                flair = None
                additional_kw["flair_err"] = "Invalid flair for this forum"
        else:
            additional_kw["flair_err"] = "Invalid flair for this forum"
    
    db.session.execute(update(Post).where(Post.id == post_id).values(**update_kw))
    return jsonify({"message" : "Post edited. It may take a few seconds for the changes to be reflected", "post_id" : post_id}), 202


@post.route("/<int:post_id>", methods=["DELETE", "OPTIONS"])
@token_required
def delete_post(post_id : int) -> Response:
    ...

@post.route("/vote/<int:post_id>", methods=["OPTIONS", "PATCH"])
@token_required
def vote_post(post_id: int) -> tuple[Response, int]:
    try:
        vote: int = int(request.args['type'])
        if vote != 0  or vote != 1:
            raise ValueError
    except KeyError:
        raise BadRequest("Vote type (upvote/downvote) not specified")
    except ValueError:
        raise BadRequest("Invalid vote value (Should be 0 (downvote) or 1 (upvote))")

    voteCounterKey: int = RedisInterface.hget(f"{Post.__tablename__}:score", post_id)
    if voteCounterKey:
        RedisInterface.incr(voteCounterKey)
        return jsonify({"message" : "Voted!"}), 202
    
    pScore: int = db.session.execute(select(Post.score).where(Post.id == post_id).with_for_update(nowait=True)).scalar_one_or_none()
    if not pScore:
        raise NotFound("No such post exists")

    voteCounterKey: str = uuid4().hex
    op = RedisInterface.set(voteCounterKey, pScore+1, nx=True)
    if not op:
        RedisInterface.incr(voteCounterKey)
        return jsonify({"message" : "Voted!"}), 202

    RedisInterface.xadd("WEAK_INSERTIONS", {"user_id" : g.REQUEST_JSON['sub'], "post_id" : post_id, 'table' : Post.__tablename__})
    RedisInterface.hset(f"{Post.__tablename__}:score", post_id, voteCounterKey)
    return jsonify({"message" : "Voted!"}), 202

@post.route("/save/<int:post_id>", methods=["OPTIONS", "PATCH"])
@token_required
def save_post(post_id: int) -> tuple[Response, int]:
    saveCounterKey: int = RedisInterface.hget(f"{Post.__tablename__}:saves", post_id)
    if saveCounterKey:
        RedisInterface.incr(saveCounterKey)
        return jsonify({"message" : "Saved!"}), 202
    
    try:
        pSaves: int = db.session.execute(select(Post.score).where(Post.id == post_id).with_for_update(nowait=True)).scalar_one_or_none()
    except:
        exc: Exception = Exception()
        exc.__setattr__("description", 'An error occured when fetching this post')
        raise exc

    if not pSaves:
        raise NotFound("No such post exists")

    saveCounterKey: str = uuid4().hex
    op = RedisInterface.set(saveCounterKey, pSaves+1, nx=True)
    if not op:
        RedisInterface.incr(saveCounterKey)
        return jsonify({"message" : "Saved!"}), 202

    RedisInterface.xadd("WEAK_INSERTIONS", {"user_id" : g.REQUEST_JSON['sub'], "post_id" : post_id, 'table' : Post.__tablename__})
    RedisInterface.hset(f"{Post.__tablename__}:saves", post_id, saveCounterKey)

    return jsonify({"message" : "Saved!"}), 202

@post.route("/report/<int:post_id>", methods=["OPTIONS", "PATCH"])
@token_required
def report_post(post_id: int) -> tuple[Response, int]:
    reportCounterKey: int = RedisInterface.hget(f"{Post.__tablename__}:reports", post_id)
    if reportCounterKey:
        RedisInterface.incr(reportCounterKey)
        return jsonify({"message" : "Reported!"}), 202
    
    pReports: int = db.session.execute(select(Post.score).where(Post.id == post_id).with_for_update(nowait=True)).scalar_one_or_none()
    if not pReports:
        raise NotFound("No such post exists")

    reportCounterKey: str = uuid4().hex
    op = RedisInterface.set(reportCounterKey, pReports+1, nx=True)
    if not op:
        RedisInterface.incr(reportCounterKey)
        return jsonify({"message" : "Reported!"}), 202
    
    RedisInterface.xadd("WEAK_INSERTIONS", {"user_id" : g.REQUEST_TOKEN['sub'], "post_id" : post_id, 'table' : Post.__tablename__})
    RedisInterface.hset(f"{Post.__tablename__}:reports", post_id, reportCounterKey)
    return jsonify({"message" : "Reported!"}), 202