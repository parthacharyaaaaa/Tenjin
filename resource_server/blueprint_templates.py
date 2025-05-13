
'''Blueprint for serving HTML files. No URL prefix for these endpoints is required'''
from flask import Blueprint, render_template, request, g, url_for
from werkzeug.exceptions import NotFound

templates: Blueprint = Blueprint('templates', __name__, template_folder='templates')

from resource_server.models import db, Post, User, Forum, ForumRules, Anime, AnimeSubscription, ForumSubscription, ForumAdmin, StreamLink
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from auxillary.utils import genericDBFetchException
from auxillary.decorators import pass_user_details

from resource_server.external_extensions import RedisInterface
import ujson

###========================= ENDPOINTS =========================###

@templates.route("/")
def index() -> tuple[str, int]:
    print(request.cookies)  
    return render_template('index.html', auth = True if request.cookies.get('access', request.cookies.get('Access')) else False)

@templates.route('/login')
def login() -> tuple[str, int]:
    return render_template('login.html', minHeader = True)

@templates.route('/signup')
def signup() -> tuple[str, int]:
    return render_template('signup.html', minHeader = True)

@templates.route('/view/forum/<string:name>')
@pass_user_details
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

        # Fetch if subscribed
        if g.requestUser:
            subbedForum = db.session.execute(select(ForumSubscription)
                                             .where((ForumSubscription.forum_id == forum.id) & (ForumSubscription.user_id == g.requestUser['sid']))).scalar_one_or_none()
        # Check if admin

            
        else:
            subbedForum = None
        if len(forumAdmins) == 6:
            forumAdmins.pop(-1)
            showAllAdminsLink: bool = True
        else:
            showAllAdminsLink: bool = False

    except SQLAlchemyError: genericDBFetchException()

    return render_template('forum.html', auth = True if request.cookies.get('access', request.cookies.get('Access')) else False,
                           name = name,
                           highlighted_posts=highlightedPosts,
                           subbed=bool(subbedForum),
                           forum=forum,
                           rules=forumRules,
                           relatedForums=relatedForums,
                           forumAdmins=forumAdmins,
                           showAllAdminsLink=showAllAdminsLink)

@templates.route('/catalogue/animes')
def get_anime() -> tuple[str, int]:
    return render_template('animes.html', auth = True if request.cookies.get('access', request.cookies.get('Access')) else False)

@templates.route('/view/anime/<int:anime_id>')
@pass_user_details
def view_anime(anime_id) -> tuple[str, int]:
    try:
        result: dict = RedisInterface.get(f"anime:{anime_id}")
        if result:
            return render_template('anime.html',
                                anime=result,
                                auth = request.cookies.get('access', request.cookies.get('Access')))
    except:
        ... #TODO: Add some logging logic for cache failures

    try:
        anime: Anime | None = db.session.execute(select(Anime).where(Anime.id == anime_id)).scalar_one_or_none()
        if not anime:
            return render_template('error.html',
                                   code = 400, 
                                   msg = 'The anime you requested could not be found :(',
                                   links = [('Back to home', url_for('.index')), ('Browse available animes', url_for('.get_anime'))])
        
        streamLinks: list[StreamLink] | None = db.session.execute(select(StreamLink)
                                                                  .where(StreamLink.anime_id == anime_id)).scalars().all()
        
        animeMapping: dict = anime.__json_like__() | {'stream_links' : {link.website:link.url for link in streamLinks}}

    except SQLAlchemyError: genericDBFetchException()

    # Cache found anime
    RedisInterface.set(f'anime:{anime_id}', ujson.dumps(animeMapping))

    isSubbed: bool = False
    try:
        isSubbed = db.session.execute(select(AnimeSubscription)
                                      .where((AnimeSubscription.anime_id == anime_id) & (AnimeSubscription.user_id == g.requestUser.get('sid')))
                                      .limit(1)).scalar_one_or_none()
    except: ... # Since anime has already been fetched, we can ignore a failure in a simple weak entity fetch for now

    return render_template('anime.html',
                           anime=animeMapping,
                           auth = request.cookies.get('access', request.cookies.get('Access')),
                           subbed = isSubbed)

@templates.route('/view/post/<int:post_id>')
def view_post(post_id: int) -> tuple[str, int]:
    return render_template('post.html',
                           auth = True if request.cookies.get('access', request.cookies.get('Access')) else False)

@templates.route("/profile/<string:username>")
def get_user(username: str) -> tuple[str, int]:
    try:
        user: User = db.session.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if not user:
            raise NotFound('No user found with this username')
    except SQLAlchemyError: genericDBFetchException()
    
    return render_template('profile.html', user=user), 200

@templates.route('/about')
def about_us() -> tuple[str, int]:
    return render_template('about.html',
                           auth = True if request.cookies.get('access', request.cookies.get('Access')) else False)


@templates.route('/legal')
def legal() -> tuple[str, int]:
    return render_template('legal_notice.html',
                           auth = True if request.cookies.get('access', request.cookies.get('Access')) else False)

@templates.route('/privacy')
def privacy() -> tuple[str, int]:
    return render_template('privacy_policy.html',
                           auth = True if request.cookies.get('access', request.cookies.get('Access')) else False)

@templates.route('/user-agreement')
def user_agreement() -> tuple[str, int]:
    return render_template('user_agreement.html',
                           auth = True if request.cookies.get('access', request.cookies.get('Access')) else False)

@templates.route('/cookies')
def cookies() -> tuple[str, int]:
    return render_template('cookie_policy.html',
                           auth = True if request.cookies.get('access', request.cookies.get('Access')) else False)

@templates.route('/contact')
def contact() -> tuple[str, int]:
    return render_template('contact_us.html',
                           auth = True if request.cookies.get('access', request.cookies.get('Access')) else False)