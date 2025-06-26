from flask import Blueprint, g, jsonify, request, redirect, url_for
from werkzeug import Response
from werkzeug.exceptions import BadRequest, NotFound, Conflict
from resource_server.resource_auxillary import update_global_counter, hset_with_ttl, resource_existence_cache_precheck, fetch_global_counters
from resource_server.external_extensions import RedisInterface
from resource_server.redis_config import RedisConfig
from resource_server.resource_decorators import token_required
from resource_server.models import db, Anime, AnimeGenre, Genre, StreamLink, Forum, AnimeSubscription
from auxillary.utils import genericDBFetchException, rediserialize, consult_cache, promote_group_ttl, fetch_group_resources, cache_grouped_resource, from_base64url, to_base64url
from sqlalchemy import select, and_, func, Row
from sqlalchemy.sql.expression import BinaryExpression
from sqlalchemy.exc import SQLAlchemyError
from flask_sqlalchemy import SQLAlchemy
import binascii
import ujson
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Sequence

ANIMES_BLUEPRINT: Blueprint = Blueprint('animes', 'animes', url_prefix="/animes")

RANDOMIZER_SQL = (select(Anime).where(Anime.id >= func.floor(func.random() * select(func.max(Anime.id)).scalar_subquery())).order_by(Anime.id).limit(50))
def getRandomAnimes(database: SQLAlchemy) -> list[Anime]:
    return database.session.execute(RANDOMIZER_SQL).scalars().all()

randomAnimes: list[Anime] = []
executor = ThreadPoolExecutor(2)

@ANIMES_BLUEPRINT.route("/<int:anime_id>")
def get_anime(anime_id: int) -> tuple[Response, int]:
    # 1: Redis
    cacheKey: str = f'anime:{anime_id}'
    animeMapping: dict = consult_cache(RedisInterface, cacheKey, RedisConfig.TTL_CAP, RedisConfig.TTL_PROMOTION, RedisConfig.TTL_EPHEMERAL, dtype='string')

    if animeMapping:
        if RedisConfig.NF_SENTINEL_KEY in animeMapping:
            raise NotFound(f"No anime with id {anime_id} could be found")
        return jsonify(animeMapping), 200            

    # 2: Fallback to DB
    try:
        anime: Anime = db.session.execute(select(Anime).where(Anime.id == anime_id)).scalar_one_or_none()
        if not anime:
            RedisInterface.set(cacheKey, RedisConfig.NF_SENTINEL_KEY, RedisConfig.TTL_EPHEMERAL)  # Announce non-existence
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
    RedisInterface.set(cacheKey, ujson.dumps(animeMapping), RedisConfig.TTL_STRONG)

    return jsonify({'anime' : animeMapping}), 200

@ANIMES_BLUEPRINT.route("/random")
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
    return redirect(url_for('.get_anime', _external=False, anime_id = anime.id))

@ANIMES_BLUEPRINT.route("/<int:anime_id>/subscribe", methods=["PATCH"])
@token_required
def sub_anime(anime_id: int) -> tuple[Response, int]:
    cacheKey: str = f'{Anime.__tablename__}:{anime_id}'
    res = RedisInterface.hgetall(cacheKey)
    if RedisInterface.hget(cacheKey, RedisConfig.NF_SENTINEL_KEY) == RedisConfig.NF_SENTINEL_VALUE:
        hset_with_ttl(RedisInterface, cacheKey, {RedisConfig.NF_SENTINEL_KEY : RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL) # Reannounce non-existence
        raise NotFound(f'No anime with ID {anime_id} exists')
    try:
        _res: Row = db.session.execute(select(Anime, AnimeSubscription)
                                       .outerjoin(AnimeSubscription, (AnimeSubscription.anime_id == anime_id) & (AnimeSubscription.user_id == g.DECODED_TOKEN['sid']))
                                       .where(Anime.id == anime_id)).first()
        if not _res:
            hset_with_ttl(RedisInterface, cacheKey, {RedisConfig.NF_SENTINEL_KEY : RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL) # Reannounce non-existence
            raise NotFound(f'No anime with ID {anime_id} exists')

        if _res[1]:
            raise Conflict("You are already subscribed to this anime")
    except SQLAlchemyError: genericDBFetchException()

    # Increment global count for animes.members and insert new record to anime_subscriptions
    update_global_counter(interface=RedisInterface, delta=1, database=db, table=Anime.__tablename__, column='members', identifier=anime_id)
    RedisInterface.xadd("WEAK_INSERTIONS", {'user_id' : g.DECODED_TOKEN['sid'], 'anime_id' : anime_id, 'table' : AnimeSubscription.__tablename__})
    
    return jsonify({'message' : 'subscribed!'}), 202

@ANIMES_BLUEPRINT.route("/<int:anime_id>/unsubscribe", methods=["PATCH"])
@token_required
def unsub_anime(anime_id: int) -> tuple[Response, int]:
    cacheKey: str = f'{Anime.__tablename__}:{anime_id}'
    res = RedisInterface.hgetall(cacheKey)
    if RedisInterface.hget(cacheKey, RedisConfig.NF_SENTINEL_KEY) == RedisConfig.NF_SENTINEL_VALUE:
        hset_with_ttl(RedisInterface, cacheKey, {RedisConfig.NF_SENTINEL_KEY : RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL) # Reannounce non-existence
        raise NotFound(f'No anime with ID {anime_id} exists')
    try:
        _res: Row = db.session.execute(select(Anime, AnimeSubscription)
                                       .outerjoin(AnimeSubscription, (AnimeSubscription.anime_id == anime_id) & (AnimeSubscription.user_id == g.DECODED_TOKEN['sid']))
                                       .where(Anime.id == anime_id)).first()
        if not _res:
            hset_with_ttl(RedisInterface, cacheKey, {RedisConfig.NF_SENTINEL_KEY : RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL) # Reannounce non-existence
            raise NotFound(f'No anime with ID {anime_id} exists')

        if not _res[1]:
            raise Conflict("You are not subscribed to this anime")
    except SQLAlchemyError: genericDBFetchException()

    # Decrement anime.members count and delete the existing anime_subscriptions record
    update_global_counter(interface=RedisInterface, delta=-1, database=db, table=Anime.__tablename__, column='members', identifier=anime_id)
    RedisInterface.xadd("WEAK_DELETIONS", {'user_id' : g.DECODED_TOKEN['sid'], 'anime_id' : anime_id, 'table' : AnimeSubscription.__tablename__})
    
    return jsonify({'message' : 'unsubscribed!'}), 202
    
@ANIMES_BLUEPRINT.route("/")
def get_animes() -> tuple[Response, int]:
    try:
        raw_cursor = request.args.get('cursor', '0').strip()
        if raw_cursor == '0':
            cursor: int = 0
        elif not raw_cursor:
            raise BadRequest("Failed to load more posts. Please refresh this page")
        else:
            cursor: int = from_base64url(raw_cursor)
        
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
    end: bool = len(animeIDs) < 6
    if not end:
        genres.pop(next(reversed(genres)))
        animes.pop(-1)
    next_cursor: str = to_base64url(animes[-1].id, length=16)

    result = [anime.__json_like__() | {'genres': genres.get(anime.id, [])} for anime in animes]
    return jsonify({'animes' : result, 'cursor' : next_cursor, 'end' : end}), 200

@ANIMES_BLUEPRINT.route("<int:anime_id>/links")
def get_anime_links(anime_id: int) -> tuple[Response, int]:
    try:
        streamLinks: dict[str, str] = dict(db.session.execute(select(StreamLink.website, StreamLink.url)
                                                    .where(StreamLink.anime_id == anime_id)).all())
    except SQLAlchemyError: genericDBFetchException()
    return jsonify({'stream_links' : streamLinks})

@ANIMES_BLUEPRINT.route("<int:anime_id>/forums")
def get_anime_forums(anime_id: int) -> tuple[Response, int]:
    try:
        raw_cursor = request.args.get('cursor', '0').strip()
        if raw_cursor == '0':
            cursor: int = 0
        else:
            cursor: int = from_base64url(raw_cursor)
    except (ValueError, TypeError, binascii.Error):
            raise BadRequest("Failed to load more forums. Please try again")

    cache_key: str = f'{Anime.__tablename__}:{anime_id}'
    pagination_cache_key: str = f'{cache_key}:{Forum.__tablename__}:{cursor}'
    anime_mapping: dict[str, Any] = resource_existence_cache_precheck(client=RedisInterface, identifier=anime_id, resource_name=Anime.__tablename__, cache_key=cache_key)

    if not anime_mapping:
        # Ensure anime exists before trying to fetch its child forums
        try:
            anime: Anime = db.session.execute(select(Anime)
                                              .where(Anime.id == anime_id)
                                              ).scalar_one_or_none()
            if not anime:
                hset_with_ttl(RedisInterface, cache_key, {RedisConfig.NF_SENTINEL_KEY:RedisConfig.NF_SENTINEL_VALUE}, RedisConfig.TTL_EPHEMERAL)
                raise NotFound(f'No anime with ID {anime_id} could be found')
            anime_mapping: dict[str, str|int] = rediserialize(anime.__json_like__())
            hset_with_ttl(RedisInterface, cache_key, anime_mapping, RedisConfig.TTL_WEAK)
        except SQLAlchemyError: genericDBFetchException()
    
    forums, end, next_cursor = fetch_group_resources(RedisInterface, group_key=pagination_cache_key)
    counter_attrs: list[str] = ['subscribers', 'posts', 'admin_count']
    if forums and all(forums):
        counters_mapping: dict[str, Sequence[int|None]] = fetch_global_counters(client=RedisInterface, hashmaps=[f'{Forum.__tablename__}:{attr}' for attr in counter_attrs], identifiers=[forum['id'] for forum in forums])

        for idx, counters in enumerate(counters_mapping.values()):
            for forum_idx, counter in enumerate(counters):
                if counter is not None:
                    forums[forum_idx][counter_attrs[idx]] = counter
        # Return paginated result with updated counters
        promote_group_ttl(RedisInterface, group_key=pagination_cache_key, promotion_ttl=RedisConfig.TTL_PROMOTION, max_ttl=RedisConfig.TTL_CAP)
        return jsonify({'posts' : forums, 'cursor' : next_cursor, 'end' : end}), 200
    # Cache miss
    try:
        where_clause: BinaryExpression = (Forum.anime == anime_id) & (Forum.deleted.is_(False))
        if cursor:
            where_clause &= (Forum.id > cursor)
        forum_res: list[Forum] = db.session.execute(select(Forum)
                                                    .where(where_clause)
                                                    .limit(6)
                                                    .order_by(Forum.created_at.desc())
                                                    ).scalars().all()
    except SQLAlchemyError: genericDBFetchException()
    if not forum_res:
        return jsonify({'forums' : None, 'end' : True, 'cursor' : cursor})
    
    end: bool = len(forum_res) < 6
    if not end:
        forum_res.pop(-1)
    next_cursor: str = to_base64url(forum_res[-1].id, length=16)
    jsonified_forums: list[dict[str, Any]] = [res.__json_like__() for res in forum_res]

    # Cache grouped resources
    cache_grouped_resource(RedisInterface, group_key=pagination_cache_key,
                           resource_type=Forum.__tablename__, resources={jsonified_forum['id'] : rediserialize(jsonified_forum) for jsonified_forum in jsonified_forums},
                           weak_ttl=RedisConfig.TTL_WEAK, strong_ttl=RedisConfig.TTL_STRONG,
                           cursor=next_cursor, end=end)
    counters_mapping: dict[str, Sequence[int|None]] = fetch_global_counters(client=RedisInterface, hashmaps=[f'{Forum.__tablename__}:{attr}' for attr in counter_attrs], identifiers=[forum['id'] for forum in jsonified_forums])

    for idx, counters in enumerate(counters_mapping.values()):
        for forum_idx, counter in enumerate(counters):
            if counter is not None:
                jsonified_forums[forum_idx][counter_attrs[idx]] = counter
    return jsonify({'forums' : jsonified_forums, 'cursor' : next_cursor, 'end' : end})
