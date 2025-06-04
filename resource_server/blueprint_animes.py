from flask import Blueprint, g, jsonify, request, redirect, url_for, current_app
from werkzeug import Response
from werkzeug.exceptions import BadRequest, NotFound, Conflict
from resource_server.resource_auxillary import update_global_counter
from resource_server.external_extensions import RedisInterface, hset_with_ttl
from resource_server.resource_decorators import token_required
from resource_server.models import db, Anime, AnimeGenre, Genre, StreamLink, Forum, AnimeSubscription
from auxillary.utils import genericDBFetchException, rediserialize, consult_cache
from sqlalchemy import select, and_, func
from sqlalchemy.exc import SQLAlchemyError
from flask_sqlalchemy import SQLAlchemy
import base64
import binascii
import ujson
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

anime = Blueprint('animes', 'animes', url_prefix="/animes")

RANDOMIZER_SQL = (select(Anime).where(Anime.id >= func.floor(func.random() * select(func.max(Anime.id)).scalar_subquery())).order_by(Anime.id).limit(50))
def getRandomAnimes(database: SQLAlchemy) -> list[Anime]:
    return database.session.execute(RANDOMIZER_SQL).scalars().all()

randomAnimes: list[Anime] = []
executor = ThreadPoolExecutor(2)

@anime.route("/<int:anime_id>")
def get_anime(anime_id: int) -> tuple[Response, int]:
    # 1: Redis
    cacheKey: str = f'anime:{anime_id}'
    animeMapping: dict = consult_cache(RedisInterface, cacheKey, current_app.config['REDIS_TTL_CAP'], current_app.config['REDIS_TTL_PROMOTION'],current_app.config['REDIS_TTL_EPHEMERAL'], dtype='string')

    if animeMapping:
        if '__NF__' in animeMapping:
            raise NotFound(f"No anime with id {anime_id} could be found")
        return jsonify(animeMapping), 200            

    # 2: Fallback to DB
    try:
        anime: Anime = db.session.execute(select(Anime).where(Anime.id == anime_id)).scalar_one_or_none()
        if not anime:
            RedisInterface.set(cacheKey, '__NF__', current_app.config['REDIS_TTL_EPHEMERAL'])  # Announce non-existence
            raise NotFound(f"No anime with id {anime_id} could be found")
        
        genres: list[str] = db.session.execute(select(Genre._name)
                                               .join(AnimeGenre, Genre.id == AnimeGenre.genre_id)
                                               .where(AnimeGenre.anime_id == anime_id)
                                               ).scalars().all()
        streamLinks: list[StreamLink] = db.session.execute(select(StreamLink)
                                                           .where(StreamLink.anime_id == anime_id)\
                                                            ).scalars().all()
    except SQLAlchemyError: genericDBFetchException()

    animeMapping = rediserialize(anime.__json_like__()) | {'genres' : genres, 'stream_links' : {link.website:link.url for link in streamLinks}}
    RedisInterface.set(cacheKey, ujson.dumps(animeMapping), current_app.config['REDIS_TTL_STRONG'])

    return jsonify({'anime' : animeMapping}), 200

@anime.route("/random")
def get_random_anime():
    global randomAnimes
    maxTries: int = 3
    found: bool = False
    try:
        for attempt in range(maxTries):
            if not randomAnimes:
                randomAnimes = getRandomAnimes(db)
            
            anime: Anime = randomAnimes.pop()
            if len(randomAnimes) < 10:
                future = executor.submit(getRandomAnimes, db)
                newRandomAnimes = future.result()
                randomAnimes.extend(newRandomAnimes)
            if anime:
                found = True
                break
        
        if not found:
            anime: Anime = db.session.execute(select(Anime)
                                            .limit(1)
                                            .order_by(Anime.id.asc())).scalar_one_or_none()
            
    
    except SQLAlchemyError: genericDBFetchException()
    return redirect(url_for('templates.view_anime', _external=False, anime_id = anime.id, random_prefetch=anime))

@anime.route("/<int:anime_id>/subscribe", methods=["PATCH"])
@token_required
def sub_anime(anime_id: int) -> tuple[Response, int]:
    cacheKey: str = f'{Anime.__tablename__}:{anime_id}'
    res = RedisInterface.hgetall(cacheKey)
    if '__NF__' in res:
        raise NotFound(f'No anime with ID {anime_id} exists')
    try:
        anime = db.session.execute(select(Anime)
                                   .where(Anime.id == anime_id)
                                   ).scalar_one_or_none()
        if not anime:
            hset_with_ttl(RedisInterface, cacheKey, {'__NF__':-1}, current_app.config['REDIS_TTL_EPHEMERAL'])
            raise NotFound(f'No anime with ID {anime_id} exists')
        anime_subscription: AnimeSubscription = db.session.execute(select(AnimeSubscription)
                                                                   .where((AnimeSubscription.anime_id == anime_id) & (AnimeSubscription.user_id == g.DECODED_TOKEN.get('sid')))
                                                                   ).scalar_one_or_none()
        if anime_subscription:
            raise Conflict("You are already subscribed to this anime")
    except SQLAlchemyError: genericDBFetchException()

    # Increment global count for animes.members and insert new record to anime_subscriptions
    update_global_counter(interface=RedisInterface, delta=1, database=db, table=Anime.__tablename__, column='members', identifier=anime_id)
    RedisInterface.xadd("WEAK_INSERTIONS", {'user_id' : g.decodedToken['sid'], 'anime_id' : anime_id, 'table' : AnimeSubscription.__tablename__})
    
    return jsonify({'message' : 'subscribed!'}), 202

@anime.route("/<int:anime_id>/unsubscribe", methods=["PATCH"])
@token_required
def unsub_anime(anime_id: int) -> tuple[Response, int]:
    try:
        anime = db.session.execute(select(Anime).where(Anime.id == anime_id)).scalar_one_or_none()
        if not anime:
            raise NotFound('No anime found with this ID')
    except SQLAlchemyError: genericDBFetchException()

    subCounterKey: str = RedisInterface.hget(f'{Anime.__tablename__}:members', anime_id)
    RedisInterface.xadd("WEAK_DELETIONS", {'user_id' : g.decodedToken['sid'], 'anime_id' : anime_id, 'table' : AnimeSubscription.__tablename__})
    if subCounterKey:
        RedisInterface.decr(subCounterKey)
        return jsonify({'message' : 'unsubscribed!'})
    
    subCounterKey = f'anime:{anime.id}:saves' 
    op = RedisInterface.set(subCounterKey, anime.members-1, nx=True)
    if not op:
        RedisInterface.decr(subCounterKey)
        return jsonify({'message' : 'unsubscribed!'})
    
    RedisInterface.hset(f"{Anime.__tablename__}:members", anime_id, subCounterKey)
    return jsonify({'message' : 'unsubscribed!'})
    
@anime.route("/")
def get_animes() -> tuple[Response, int]:
    try:
        rawCursor = request.args.get('cursor', '0').strip()
        if rawCursor == '0':
            cursor = 0
        elif not rawCursor:
            raise BadRequest("Failed to load more posts. Please refresh this page")
        else:
            cursor = int(base64.b64decode(rawCursor).decode())

        searchParam: str = request.args.get('search', '').strip()
        genreID: str = request.args.get('genre', None)
        if genreID:
            if not genreID.isnumeric():
                genreID = None
            else:
                genreID = int(genreID)


    except (ValueError, TypeError, binascii.Error):
            raise BadRequest("Failed to load more posts. Please refresh this page")
    
    try:
        whereClause = [Anime.id > cursor]
        if searchParam:
            whereClause.append(Anime.title.ilike(f"%{searchParam}%"))   # searchParam is a string anyways, so we can safely inject it in an expression >:3

        if not genreID:
            animes: list[Anime] = db.session.execute(select(Anime)
                                                    .where(and_(*whereClause))
                                                    .order_by(Anime.id.asc())
                                                    .limit(10)).scalars().all()
        else:
            animes: list[Anime] = db.session.execute(select(Anime)
                                                    .where(and_(*whereClause))
                                                    .order_by(Anime.id.asc())
                                                    .join(AnimeGenre, AnimeGenre.anime_id == Anime.id)
                                                    .where(AnimeGenre.genre_id == genreID)
                                                    .limit(10)).scalars().all()

        if not animes:
            return jsonify({'animes' : None})
         
        animeIDs = [x.id for x in animes]
         
        rows = db.session.execute(select(AnimeGenre.anime_id, Genre._name)
                                   .join(Genre, Genre.id == AnimeGenre.genre_id)
                                   .where(AnimeGenre.anime_id.in_(animeIDs))).all()
         
        genres: dict[int, list[str]] = defaultdict(list)
        for anime_id, genre_name in rows:
            genres[anime_id].append(genre_name)
    except SQLAlchemyError: genericDBFetchException()

    cursor = base64.b64encode(str(animes[-1].id).encode('utf-8')).decode()
    result = [anime.__json_like__() | {'genres': genres.get(anime.id, [])} for anime in animes]
    return jsonify({'animes' : result, 'cursor' : cursor}), 200

@anime.route("<int:anime_id>/links")
def get_anime_links(anime_id: int) -> tuple[Response, int]:
    try:
        streamLinks: dict[str, str] = dict(db.session.execute(select(StreamLink.website, StreamLink.url)
                                                    .where(StreamLink.anime_id == anime_id)).all())
    except SQLAlchemyError: genericDBFetchException()
    return jsonify({'stream_links' : streamLinks})

@anime.route("<int:anime_id>/forums")
def get_anime_forums(anime_id: int) -> tuple[Response, int]:
    try:
        rawCursor = request.args.get('cursor', '0').strip()
        if rawCursor == '0':
            cursor = 0
        elif not rawCursor:
            raise BadRequest("Failed to load more posts. Please refresh this page")
        else:
            cursor = int(base64.b64decode(rawCursor).decode())
    except (ValueError, TypeError, binascii.Error):
            raise BadRequest("Failed to load more posts. Please refresh this page")
    try:
        forums: list[Forum] = db.session.execute(select(Forum)
                                                 .where((Forum.id > cursor) & (Forum.anime == anime_id))
                                                 .limit(10)).scalars().all()
        if not forums:
            return jsonify({'forums' : [], 'cursor' : rawCursor})
    except SQLAlchemyError: genericDBFetchException()

    cursor = base64.b64encode(str(forums[-1].id).encode('utf-8')).decode()


    _res: list[dict] = [forum.__json_like__() for forum in forums]
    return jsonify({'forums' : _res, 'cursor' : cursor}), 200