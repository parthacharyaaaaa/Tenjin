
'''Blueprint for serving HTML files. No URL prefix for these endpoints is required'''
from flask import Blueprint, render_template, request, g, url_for, current_app

templates: Blueprint = Blueprint('templates', __name__, template_folder='templates')

from resource_server.models import db, Post, User, Forum, ForumRules, Anime, AnimeSubscription, ForumSubscription, ForumAdmin, StreamLink, AnimeGenre, Genre
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from auxillary.utils import genericDBFetchException, rediserialize, consult_cache
from resource_server.resource_decorators import pass_user_details

from resource_server.external_extensions import RedisInterface, hset_with_ttl
import ujson
from typing import Any
###========================= ENDPOINTS =========================###

@templates.context_processor
def global_ctx_injector() -> dict[str, Any]:
    return {'auth' : request.cookies.get('access', request.cookies.get('Access')),
            'user_link' : None if not getattr(g, 'REQUESTING_USER', None) else url_for('.get_user', _external=False, username=g.REQUESTING_USER.get('sub'))}

@templates.route("/")
@pass_user_details
def index() -> tuple[str, int]:
    return render_template('index.html')

@templates.route('/login')
def login() -> tuple[str, int]:
    return render_template('login.html')

@templates.route('/signup')
def signup() -> tuple[str, int]:
    return render_template('signup.html')

@templates.route("/forgot-password")
def forgot_password() -> tuple[str, int]:
    return render_template('forgot_password.html')

@templates.route("/recover-password/<string:digest>")
def recover_password(digest: str) -> tuple[str, int]:
    return render_template('change_password.html')

@templates.route('/view/forum/<string:name>')
@pass_user_details
def forum(name: str) -> tuple[str, int]:
    pointerKey: str = f'forum_pointer:{name}'
    forumMapping: dict[str, Any] = None
    try:
        forumID = RedisInterface.get(pointerKey)
        if forumID == '__NF__':
            return render_template('error.html',
                        code=404,
                        message='No forum with this name could be found',
                        links = [('Back to home', url_for('.index'))])
        if forumID:
            RedisInterface.expire(pointerKey, current_app.config['REDIS_TTL_WEAK'])

            cacheKey: str = f'forum:{forumID}'
            forumMapping = consult_cache(RedisInterface, cacheKey, current_app.config['REDIS_TTL_CAP'], current_app.config['REDIS_TTL_PROMOTION'],current_app.config['REDIS_TTL_EPHEMERAL'], dtype='string')

            if forumMapping and '__NF__' in forumMapping:
                return render_template('error.html',
                                       code=404,
                                       message='No forum with this name could be found',
                                       links = [('Back to home', url_for('.index'))])
    except: ... # Silent, fallback to DB

    if not forumMapping:
        try:
            # Fetch forum details
            forum: Forum = db.session.execute(select(Forum)
                                              .where((Forum._name == name) & (Forum.deleted.is_(False)))
                                              ).scalar_one_or_none()
            if not forum:
                # Announce string representation as __NF__
                RedisInterface.set(pointerKey, '__NF__', current_app.config['REDIS_TTL_EPHEMERAL'])

                return render_template('error.html',
                                       code=404,
                                       message='No forum with this name could be found',
                                       links = [('Back to home', url_for('.index'))])
            
            # Fetch highlighted posts
            highlightedPosts: list[Post] = db.session.execute(select(Post)
                                                              .where(Post.id.in_([forum.highlight_post_1, forum.highlight_post_2, forum.highlight_post_3]))
                                                              ).scalars().all()

            # Fetch rules
            forumRules: list[ForumRules] = db.session.execute(select(ForumRules)
                                                              .where(ForumRules.forum_id == forum.id)
                                                              ).scalars().all()

            # Fetch admins
            forumAdmins: list[str] = db.session.execute(select(User.username)
                                                            .join(ForumAdmin, ForumAdmin.forum_id == forum.id)
                                                            .where(User.id == ForumAdmin.user_id)
                                                            .limit(6)
                                                            ).scalars().all()

        except SQLAlchemyError: genericDBFetchException()

        showAllAdminsLink: bool = False
        if len(forumAdmins) == 6:
            forumAdmins.pop(-1)
            showAllAdminsLink = True

        #TODO: Cache highlighted posts separately
        forumMapping: dict = {'forum' : rediserialize(forum.__json_like__()),
                              'forum_rules' : tuple(forumRule.__json_like__() for forumRule in forumRules),
                              'forum_admins' : forumAdmins,
                              'highlighted_posts' : tuple(highlightedPost.__json_like__() for highlightedPost in highlightedPosts)}
        
        # Cache forum
        RedisInterface.set(f'forum:{forumID}', ujson.dumps(forumMapping), current_app.config['REDIS_TTL_STRONG'])

    # Post has now been fetched, either from cache or from DB
    subbedForum: bool = False
    forumAdminRole: str = None
    if g.REQUESTING_USER and 'fetch_relation' in request.args:
        try:
            subbedForum = bool(db.session.execute(select(ForumSubscription)
                                            .where((ForumSubscription.forum_id == forumMapping['forum']['id']) & (ForumSubscription.user_id == g.REQUESTING_USER['sid']))
                                            ).scalar_one_or_none())

            forumAdminRole: str = db.session.execute(select(ForumAdmin.role)
                                                     .where((ForumAdmin.forum_id == forumMapping['forum']['id']) & (ForumAdmin.user_id == g.REQUESTING_USER.get('sid')))
                                                     ).scalar_one_or_none()
        except SQLAlchemyError: ... # Silent failure, very wasteful to let the entire cycle go to waste over 2 optional weak entities        

    return render_template('forum.html', forum_mapping = forumMapping, subbed=bool(subbedForum))

@templates.route('/catalogue/animes')
@pass_user_details
def get_anime() -> tuple[str, int]:
    return render_template('animes.html')

@templates.route('/view/anime/<int:anime_id>')
@pass_user_details
def view_anime(anime_id) -> tuple[str, int]:
    cacheKey: str = f'anime:{anime_id}'
    # 1: Redis
    animeMapping: dict = consult_cache(RedisInterface, cacheKey, current_app.config['REDIS_TTL_CAP'], current_app.config['REDIS_TTL_PROMOTION'],current_app.config['REDIS_TTL_EPHEMERAL'], dtype='string')
    
    if animeMapping:
        if '__NF__' in animeMapping:
            return render_template('error.html',
                                    code = 400, 
                                    msg = 'The anime you requested could not be found :(',
                                    links = [('Back to home', url_for('.index')), ('Browse available animes', url_for('.get_anime'))])
    
        if not g.REQUESTING_USER:
            # No auth, return early
            return render_template('anime.html', anime=animeMapping, subbed=False)

    else:
        try:
            anime: Anime = db.session.execute(select(Anime)
                                            .where(Anime.id == anime_id)
                                            ).scalar_one_or_none()
            if not anime:
                RedisInterface.set(cacheKey, '__NF__', current_app.config['REDIS_TTL_EPHEMERAL'])  # Announce non-existence
                return render_template('error.html',
                                    code = 400, 
                                    msg = 'The anime you requested could not be found :(',
                                    links = [('Back to home', url_for('.index')), ('Browse available animes', url_for('.get_anime'))])
            
            streamLinks: list[StreamLink] = db.session.execute(select(StreamLink)
                                                            .where(StreamLink.anime_id == anime_id)
                                                            ).scalars().all()
            
            genres: list[str] = db.session.execute(select(Genre._name)
                                                .join(AnimeGenre, Genre.id == AnimeGenre.genre_id)
                                                .where(AnimeGenre.anime_id == anime_id)).scalars().all()
            
            # Finally, write to animeMapping
            animeMapping: dict = rediserialize(anime.__json_like__()) | {'stream_links' : {link.website:link.url for link in streamLinks}, 'genres' : genres}
        except SQLAlchemyError: genericDBFetchException()

    if 'random_prefetch' not in request.args:
        # Only cache if user actually wanted to come here, and not randomly
        RedisInterface.set(cacheKey, ujson.dumps(animeMapping), current_app.config['REDIS_TTL_STRONG'])

    # Check user's subscription to this anime
    isSubbed: bool = False
    if g.REQUESTING_USER:
        try:
            isSubbed = db.session.execute(select(AnimeSubscription)
                                        .where((AnimeSubscription.anime_id == anime_id) & (AnimeSubscription.user_id == g.REQUESTING_USER.get('sid')))
                                        .limit(1)).scalar_one_or_none()
        except: ... # Since anime has already been fetched, we can ignore a failure in a simple weak entity fetch for now. isSubbed will remain as False anyways, which is a safe fallback

    return render_template('anime.html',
                           anime=animeMapping,
                           subbed = bool(isSubbed))

@templates.route('/view/post/<int:post_id>')
@pass_user_details
def view_post(post_id: int) -> tuple[str, int]:
    cacheKey: str = f'post:{post_id}'
    postMapping: dict = consult_cache(RedisInterface, cacheKey, current_app.config['REDIS_TTL_CAP'], current_app.config['REDIS_TTL_PROMOTION'],current_app.config['REDIS_TTL_EPHEMERAL'])

    if postMapping:
        if '__NF__' in postMapping:
            return render_template('error.html',
                                    code = 400, 
                                    msg = 'The post you requested could not be found :(',
                                    links = [('Back to home', url_for('.index'))])


        if not g.REQUESTING_USER:
            return render_template('post.html',
                                post_mapping =  postMapping,
                                permission_level = 0)
    
    else:
        try:
            post: Post = db.session.execute(select(Post)
                                            .where((Post.id == post_id) & (Post.deleted.is_(False)))
                                            ).scalar_one_or_none()
            if not post:
                hset_with_ttl(RedisInterface, cacheKey, {'__NF__':-1}, current_app.config['REDIS_TTL_EPHEMERAL'])
            
            author: User = db.session.execute(select(User)
                                        .where(User.id == post.author_id)
                                        ).scalar_one_or_none()
            
            postMapping: dict = post.__json_like__() | {'author' : None if author.deleted else author.username, 'author_id' : None if author.deleted else author.id}
            # Cache post
            hset_with_ttl(RedisInterface, cacheKey, rediserialize(postMapping), current_app.config['REDIS_TTL_STRONG'])
        except SQLAlchemyError: genericDBFetchException()
        
    # Post fetched, now check to see user's relation to this post
    permissionLevel: int = 0
    if g.REQUESTING_USER:
        if(g.REQUESTING_USER.get('sid') == postMapping.get('author_id')):
            # Author has highest permission level with edit access also
            permissionLevel = 2
        else:
            try:
                # If admin, permission level is 1
                permissionLevel = int(bool(db.session.execute(select(ForumAdmin)
                                                            .where((ForumAdmin.forum_id == postMapping.get('forum')) & (ForumAdmin.user_id == g.REQUESTING_USER.get('sid')))
                                                            ).scalar_one_or_none()))
            except SQLAlchemyError: ... # No need to fail at this stage, just fallback to no permissions for now
            

    return render_template('post.html',
                           post_mapping = postMapping,
                           permission_level = permissionLevel)

@templates.route("/profile/<string:username>")
@pass_user_details
def get_user(username: str) -> tuple[str, int]:
    cacheKey: str = f'user:{username}'
    userMapping: dict = consult_cache(RedisInterface, cacheKey, current_app.config['REDIS_TTL_CAP'], current_app.config['REDIS_TTL_PROMOTION'],current_app.config['REDIS_TTL_EPHEMERAL'])

    if userMapping:
        if '__NF__' in userMapping:
            return render_template('error.html',
                                    code=404,
                                    msg='No user with this username could be found',
                                    links = [('Back to home', url_for('.index'))])
        return render_template('profile.html', user_mapping=userMapping), 200
    
    try:
        user: User = db.session.execute(select(User)
                                        .where((User.username == username) & (User.deleted.is_(False)))
                                        ).scalar_one_or_none()
        if not user:
            hset_with_ttl(RedisInterface, cacheKey, {'__NF__' : -1}, current_app.config['REDIS_TTL_EPHEMERAL']) # Announce non-existence of this resource
            return render_template('error.html',
                                   code=404,
                                   msg='No user with this username could be found',
                                   links = [('Back to home', url_for('.index'))])
                                   
    except SQLAlchemyError: genericDBFetchException()

    userMapping: dict = rediserialize(user.__json_like__())
    hset_with_ttl(RedisInterface, cacheKey, userMapping, current_app.config['REDIS_TTL_STRONG'])
    return render_template('profile.html', user_mapping=userMapping), 200

@templates.route('/about')
@pass_user_details
def about_us() -> tuple[str, int]:
    return render_template('about.html')
                           
@templates.route('/legal')
@pass_user_details
def legal() -> tuple[str, int]:
    return render_template('legal_notice.html')
                           

@templates.route('/privacy')
@pass_user_details
def privacy() -> tuple[str, int]:
    return render_template('privacy_policy.html')
                           

@templates.route('/user-agreement')
@pass_user_details
def user_agreement() -> tuple[str, int]:
    return render_template('user_agreement.html')
                           

@templates.route('/cookies')
@pass_user_details
def cookies() -> tuple[str, int]:
    return render_template('cookie_policy.html')
                           

@templates.route('/contact')
@pass_user_details
def contact() -> tuple[str, int]:
    return render_template('contact_us.html')