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


@post.route("/<int:post_id>", methods=["PATCH", "OPTIONS"])
def edit_post(post_id : int) -> Response:
    ...

@post.route("/<int:post_id>", methods=["DELETE", "OPTIONS"])
def delete_post(post_id : int) -> Response:
    ...
