
'''Blueprint for serving HTML files. No URL prefix for these endpoints is required'''
from flask import Blueprint, render_template, request
from werkzeug.exceptions import NotFound

templates: Blueprint = Blueprint('templates', __name__, template_folder='templates')

from resource_server.models import db, Post, User, Forum, ForumRules, Anime, AnimeSubscription, ForumSubscription, ForumAdmin
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from auxillary.utils import genericDBFetchException

###========================= ENDPOINTS =========================###

@templates.route("/")
def index() -> tuple[str, int]:
    print(request.cookies)
    return render_template('base.html', auth = True if request.cookies.get('access', request.cookies.get('Access')) else False)

@templates.route('/login')
def login() -> tuple[str, int]:
    return render_template('login.html', minHeader = True)

@templates.route('/signup')
def signup() -> tuple[str, int]:
    return render_template('signup.html', minHeader = True)

@templates.route('/view/forum/<string:name>')
def forum(name: str) -> tuple[str, int]:
    try:
        # Fetch forum details
        forum: Forum = db.session.execute(select(Forum).where(Forum._name == name)).scalar_one_or_none()
        if not forum:
            raise NotFound('This forum does not exist')
        
        # Fetch highlighted posts
        highlightedPosts: list[Post] = db.session.execute(select(Post).where(Post.id.in_([forum.highlight_post_1, forum.highlight_post_2, forum.highlight_post_3]))).scalars().all()

        # Fetch rules
        forumRules: list[ForumRules] = db.session.execute(select(ForumRules).where(ForumRules.forum_id == forum.id)).scalars().all()

        # Fetch admins
        forumAdmins: list[ForumAdmin] = db.session.execute(select(User.username)
                                                           .where(User.id == ForumAdmin.user_id)
                                                           .join(ForumAdmin, User.id == ForumAdmin.user_id)
                                                           .limit(6)).scalars().all()
        
        # Fetch related forums (if any)
        relatedForums: list[Forum] = db.session.execute(select(Forum._name).where(Forum.anime == forum.anime).limit(3)).scalars().all()
        
        if len(forumAdmins) == 6:
            forumAdmins.pop(-1)
            showAllAdminsLink: bool = True
        else:
            showAllAdminsLink: bool = False

    except SQLAlchemyError: genericDBFetchException()

    return render_template('forum.html', auth = True if request.cookies.get('access', request.cookies.get('Access')) else False,
                           name = name,
                           highlighted_posts=highlightedPosts,
                           forum=forum,
                           rules=forumRules,
                           relatedForums=relatedForums,
                           forumAdmins=forumAdmins,
                           showAllAdminsLink=showAllAdminsLink)

@templates.route('/catalogue/animes')
def get_anime() -> tuple[str, int]:
    return render_template('animes.html', auth = True if request.cookies.get('access', request.cookies.get('Access')) else False)

@templates.route('/view/anime/<int:anime_id>')
def view_anime(anime_id) -> tuple[str, int]:
    try:
        anime = db.session.execute(select(Anime).where(Anime.id == anime_id)).scalar_one_or_none()
        if not anime:
            raise NotFound("No anime with this ID could be found")
        
        
    except SQLAlchemyError: genericDBFetchException()
    return render_template('anime.html',
                           auth = True if request.cookies.get('access', request.cookies.get('Access')) else False)

@templates.route("/profile/<string:username>")
def get_user(username: str) -> tuple[str, int]:
    try:
        user: User = db.session.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if not user:
            raise NotFound('No user found with this username')
    except SQLAlchemyError: genericDBFetchException()
    
    return render_template('profile.html', user=user), 200    