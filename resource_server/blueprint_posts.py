from flask import Blueprint, Response, jsonify, g, url_for
post = Blueprint("post", "post", url_prefix="/posts")

from sqlalchemy import select
from resource_server.models import db, Post, User, Forum, forum_flairs
from auxillary.decorators import enforce_json
from resource_server import red

from werkzeug.exceptions import NotFound, BadRequest

@enforce_json
@post.route("/", methods=["POST", "OPTIONS"])
def create_post() -> Response:
    if not (g.REQUEST_JSON.get('author') and
            g.REQUEST_JSON.get('forum') and 
            g.REQUEST_JSON.get('title') and
            g.REQUEST_JSON.get('body')):
        raise BadRequest("Invalid details for creating a post")

    try:
        postAuthor: str = str(g.REQUEST_JSON.get('author'))
        forumID: int = int(g.REQUEST_JSON['forum'])
        title: str = g.REQUEST_JSON['title'].strip()
        body: str = g.REQUEST_JSON['body'].strip()

        if g.REQUEST_JSON.get('flair'):
            flair: str = g.REQUEST_JSON['flair'].strip()

    except (KeyError, ValueError):
        raise BadRequest("Malformatted post details")
    
    # Ensure author and forum actually exist
    author: User = db.session.execute(select(User).where(User.id == postAuthor)).scalar_one_or_none()
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
        
    # All checks done, push to insert stream
    ...

@post.route("/<int:post_id>", methods=["GET", "OPTIONS"])
def get_post(post_id : int) -> Response:
    post : Post | None = db.session.execute(select(Post).where(Post.id == post_id)).scalar_one_or_none()
    if not post:
        raise NotFound(f"Post with id {post_id} could not be found :(")
    return jsonify(post.__json_like__()), 200

@enforce_json
@post.route("/<int:post_id>", methods=["PATCH", "OPTIONS"])
def edit_post(post_id : int) -> Response:
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
    
    # All checks done, push to updation stream
    ...
    return jsonify({"message" : "Post edited. It may take a few seconds for the changes to be reflected", "post_id" : post_id}), 202


@post.route("/<int:post_id>", methods=["DELETE", "OPTIONS"])
def delete_post(post_id : int) -> Response:
    ...
