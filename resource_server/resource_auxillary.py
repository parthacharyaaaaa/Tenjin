'''Auxillary functions exclusive to the resource server'''
import requests
import re
from auxillary.utils import from_base64url
import ecdsa
from redis import Redis
from redis.client import Pipeline
from flask import Flask
from werkzeug.exceptions import Gone, Conflict, NotFound
from resource_server.redis_config import RedisConfig
import time
from traceback import format_exc
from sqlalchemy import text
from flask_sqlalchemy import SQLAlchemy
from typing import Any, Mapping, Optional, Sequence
from types import FunctionType

EMAIL_REGEX = r"^(?=.{1,320}$)([a-zA-Z0-9!#$%&'*+/=?^_`{|}~.-]{1,64})@([a-zA-Z0-9.-]{1,255}\.[a-zA-Z]{2,16})$"     # RFC approved babyyyyy

def poll_global_key_mapping(interface: Redis) -> dict[str, bytes]:
    '''Poll "JWKS_MAPPING" hashmap in Redis for new key mapping'''
    res: dict[str, str] = interface.hgetall('JWKS_MAPPING')
    if not res:
        raise RuntimeError('JWKS Mapping not found/ found but empty')
    
    return {kid : pub_pem.encode() for kid, pub_pem in res.items()}  # interface has decoded responses, but PyJWT needs public pem in bytes

def hset_with_ttl(interface: Redis, name: str, mapping: dict, ttl: int, transaction: bool = True):
    with interface.pipeline(transaction) as pp:
        pp.hset(name=name, mapping=mapping)
        pp.expire(name=name, time=ttl)
        pp.execute()

def batch_hset_with_ttl(interface: Redis, names: Sequence[str], mappings: Sequence[dict], ttl: int, transaction: bool = True):
    if len(names) != len(mappings):
        raise ValueError('Names and mappings do not match')
    
    with interface.pipeline(transaction) as pp:
        for idx, mapping in enumerate(mappings):
            pp.hset(name=names[idx], mapping=mapping)
            pp.expire(name=names[idx], time=ttl)
        pp.execute()

def update_jwks(endpoint: str, currentMapping: dict[str, bytes], interface: Redis, lock_ttl: int = 300, jwks_poll_cooldown: int = 300, timeout: int = 3, max_global_mapping_polls: int = 10) -> dict[str, str|int]:
    '''Fetch JWKS from auth server and load any new key mappings into currentMapping'''
    res: int = interface.set('JWKS_POLL_LOCK', 1, ex=lock_ttl, nx=True)
    if not res:
        # Another worker is currently polling JWKS, wait until lock is released and then read global key mapping
        while(interface.get('JWKS_POLL_LOCK') and max_global_mapping_polls):
            print(f'[JWKS POLLER] Standing by for master thread to perform updation')
            time.sleep(timeout*2)
            max_global_mapping_polls-=1 # Ideally, the lock would always be released no matter what, but a fallback to stop the thread from waiting forever wouldn't hurt
        
        global_mapping: dict[str, str] = poll_global_key_mapping(interface=interface) 
        
        return {kid:pub_pem.encode() for kid, pub_pem in global_mapping.items()}
    
    try:
        print('[JWKS POLLER] Attempting to update JWKS...')
        response: requests.Response = requests.get(endpoint, timeout=timeout)
        if response.status_code != 200:
            return currentMapping
        
        newMapping: dict[str, str|int] = response.json().get('keys')
        if not newMapping:
            #TODO: Ping auth server to indicate malformatted JWKS response
            return currentMapping
    
        local_keys: frozenset[str] = frozenset(currentMapping.keys())
        global_valid_keys: frozenset[str] = frozenset(mapping['kid'] for mapping in newMapping)

        # Purge local keys that are invalid
        expired_keys: frozenset[str] = local_keys - global_valid_keys
        for expired_key in expired_keys:
            currentMapping.pop(expired_key)
        
        # For any new items in newMapping, we'll need to construct a new dict entry with its public verificiation key
        for keyMetadata in newMapping:
            # New key found, welcome to the club >:3
            if keyMetadata['kid'] not in currentMapping:
                x = from_base64url(keyMetadata['x'])
                y = from_base64url(keyMetadata['y'])
                point = ecdsa.ellipticcurve.Point(ecdsa.SECP256k1.curve, x, y)
                vk = ecdsa.VerifyingKey.from_public_point(point, curve=ecdsa.SECP256k1)

                currentMapping[keyMetadata['kid']] = vk.to_pem()

        # Update global list and values in Redis to inform other workers
        with interface.pipeline() as pipe:
            # Overwrite mapping entirely
            pipe.delete('JWKS_MAPPING')
            pipe.hset('JWKS_MAPPING', mapping=currentMapping)

            # Finally release lock and update cooldown
            pipe.delete('JWKS_POLL_LOCK')
            pipe.set('JWKS_POLL_COOLDOWN', value=1, ex=jwks_poll_cooldown)
            pipe.execute()

        return currentMapping
    except Exception:
        with interface.pipeline() as pipe:
            pipe.delete('JWKS_POLL_LOCK')
            pipe.set('JWKS_POLL_COOLDOWN', value=1, ex=jwks_poll_cooldown)
            pipe.execute()
        print(format_exc())
        return currentMapping

def background_poll(current_app: Flask, interface: Redis, interval: int = 300, lock_ttl: int = 300) -> None:
    '''Poll Redis and JWKS endpoints indefinitely to keep a given app's mappings consistent
    Args:
        current_app: Flask instance for which the poll needs to be done
        interface: Redis instance connected to server holding the `JWKS_MAPPING` hashmap
        interval: Time in seconds to wait before subsequent polls
        lock_ttl: Time in seconds for a lock to persist when polling
    '''
    while True:
        try:
            update_jwks(endpoint=f'{current_app.config["AUTH_SERVER_URL"]}/auth/jwks.json',
                        currentMapping= current_app.config['KEY_VK_MAPPING'],
                        lock_ttl=lock_ttl,
                        interface=interface)
        except Exception:
            print(f"[JWKS POLLER]: Error: {format_exc()}")
        finally:
            time.sleep(interval)

def processUserInfo(**kwargs) -> tuple[bool, dict]:
    '''Validate and process user details\n
    Currently accepts params:
    - username (str)
    - password (str)
    - email (str)

    returns:
    tuple of boolean and dictionary. In case of failure in validation, the bool value is False, and the immediate error message is contained in the dict. Otherwise, boolean is True and the dict contains the processed user data
    '''
    global EMAIL_REGEX
    try:
        res: dict[str, str] = {}
        if kwargs.get("username"):
            username: str = kwargs['username'].strip()
            if not (5 < len(username) < 64):
                return False, {"error" : "username must not end or begin with whitespaces, and must be between 5 and 64 characters long"}
            if not username.isalnum():
                return False, {"error" : "username must be strictly alphanumeric"}
            res['username'] = username
        
        if kwargs.get("email"):
            email: str = kwargs['email'].strip()
            if not re.match(EMAIL_REGEX, email, re.IGNORECASE):
                print("Oh allah")
                return False, {"error" : "invalid email address"}
            res['email'] = email

        if kwargs.get('password'):
            if not (8 < len(kwargs.get('password')) < 64):
                return False, {"error" : "Password length must lie between 8 and 64"}
            res['password'] = kwargs.get('password')

        return True, res
    except:
        print(format_exc())
        return False, {"error" : "Malformatted data, please validate data types of each field"}
  
def update_global_counter(interface: Redis, delta: int, database: SQLAlchemy, table: str, column: str, identifier: int | str, hashmap_key: str = None) -> None:
    '''
    Update the global counter for a resource's field
    Args:
        interface: Redis instance connected to server holding the counters
        delta: Whether to increment or decrement the counter
        database: SQLAlchemy instance to fetch data from in case the counter is absent in Redis, and a new one needs to be made
        table: Table name for the entity to be updated
        column: Column of entity associated with the counter
        identifier: Unique ID to identify the target record
        hashmap_key: Optional key name for hashmap. If not passed, constructed as table:column
    '''
    if not hashmap_key:
        hashmap_key: str = f'{table}:{column}'
    counter = interface.hget(hashmap_key, identifier)
    if counter:
        interface.hincrby(hashmap_key, identifier, delta)
        return 

    # No counter, create one
    currentCount: int = database.session.execute(text(f"SELECT {column} FROM {table} WHERE id = :identifier"), {'identifier':identifier}).scalar()
    
    op = interface.hsetnx(hashmap_key, identifier, currentCount+delta)
    if not op:
        # Counter made by another worker, update in place
        interface.hincrby(hashmap_key, identifier, delta)

def fetch_global_counters(interface: Redis, *counter_names: str) -> list[int]:
    counters: list[int] = []
    with interface.pipeline(transaction=False) as pipe:
        for counter_name in counter_names:
            pipe.get(counter_name)
        counters = pipe.execute()
    return list(map(lambda counter:None if not counter else int(counter), counters))

def pipeline_exec(client: Redis, op_mapping: Mapping[FunctionType, Mapping[str, Any]], transaction: bool = False) -> None:
    """
    Execute multiple Redis operations in a single network round-trip using a pipeline.
    Args:
        client (redis.Redis): An instance of redis.Redis connected to the target Redis server.
        op_mapping (Mapping[FunctionType, Mapping[str, Any]]): 
            A mapping of unbound Pipeline function references to dictionaries of keyword arguments.
            Example:
                {
                    Redis.set: {"key": "example", "value": "example", "nx": True},
                    Redis.xadd: {"name": "mystream", "fields": {"event": "login"}}
                }
        transaction (bool, optional): 
            Whether to execute the pipeline within a MULTI/EXEC transaction block. 
            Defaults to False.
    """
    pipe: Pipeline = client.pipeline(transaction=transaction)
    try:
        for operation, kwargs in op_mapping.items():
            if not isinstance(operation, FunctionType):
                raise TypeError(f"{operation} must be an unbound Pipeline method reference (e.g., Pipeline.set, Pipeline.xadd)")
            operation(pipe, **kwargs)   # Pass pipeline instance as self
        pipe.execute()
    finally:
        pipe.close()

def posts_cache_precheck(client: Redis, post_id: str, post_cache_key: str, post_deletion_intent_flag: str, action_flag: str, lock_name: str, conflicting_intent: Optional[str] = None) -> tuple[Optional[dict], Optional[str]]:
    '''
    Consult cache and perform a check on a given post to try to validate the request thriugh cache and minimize DB lookups. Although this cannot guarantee post validity, if a post is found to be invalid through cache, an appropriate HTTP exception is raised. If all checks pass, then the post_mapping (if found) and the latest intent (if found) are returned
    Args:
        client: Redis client instance connected to cache server
        post_id: Unique identifier for post
        post_cache_key: cache key for this post
        post_deletion_intent_flag: Deletion flag name for this post
        action_flag: Flag name to check for latest user intent for this post
        lock_name: Name of lock for this action
        conflicting_intent: If specified, latest user intent is checked against this value for early rejection

    Returns:
        post_mapping (dict), latest_intent (str)
    '''
    with client.pipeline() as pipe:
        # Post
        pipe.hgetall(post_cache_key)
        pipe.get(post_deletion_intent_flag)  # Existence alone is enough to prove post deletion
        
        pipe.get(action_flag)   # Get latest intent (if any) for this action
        pipe.get(lock_name)

        post_mapping, post_deletion_intent, latest_intent, lock = pipe.execute()

    if post_deletion_intent:    # Deletion written in cache
        raise Gone('This post has been permanently deleted, and will soon be unavailable')
    if post_mapping and RedisConfig.NF_SENTINEL_KEY in post_mapping:    # Post non-existence written in cache
        hset_with_ttl(client, post_cache_key, post_mapping, RedisConfig.TTL_EPHEMERAL)  # Reannounce post non-existence
        raise NotFound(f'No post with ID {post_id} could be found (Never existed, or deleted)')
    if lock:    # Stop race condition early
        raise Conflict('Another worker is processing this exact request at the moment')
    if (latest_intent and not conflicting_intent) or (conflicting_intent and latest_intent == conflicting_intent):  # Stop duplicate requests
        raise Conflict(f'This action for post {post_id} has already been requested')
    
    return post_mapping, latest_intent  # post_mapping and latest intent may be required by the endpoint later

def resource_existence_cache_precheck(client: Redis, identifier: int|str, resource_name: str, cache_key: Optional[str] = None, deletion_flag_key: Optional[str] = None) -> dict[str, Any]:
    '''Generic check for a resource's existence in cache. Raises appropriate HTTP exception if non-existence is guarenteed
    Args:
        client: Redis client connected to cache server
        identifier: Unique identifier (Typically PK) of resource
        resource_name: Cache key for this resource
        deletion_flag_key: Cache key for deletion flag for this resource
        
    Returns:
        resource_mapping (dict[str, Any]) on cache hit'''
    if not cache_key: 
        cache_key: str = f'{resource_name}:{identifier}'
    if not deletion_flag_key:
        deletion_flag_key: str = f'delete:{cache_key}'
     
    with client.pipeline() as pipe:
        pipe.hgetall(cache_key)
        pipe.get(deletion_flag_key)
        resource_mapping, deletion_intent = pipe.execute()
    if deletion_intent:
        raise Gone('This resource was just deleted')
    if resource_mapping and RedisConfig.NF_SENTINEL_KEY in resource_mapping:
        hset_with_ttl(client, cache_key, resource_mapping, RedisConfig.TTL_EPHEMERAL)
        raise NotFound(f"No {resource_name} with ID {identifier} found")
    
    return resource_mapping

def resource_cache_precheck(client: Redis, identifier: int|str, cache_key: str, deletion_intent_flag: str, action_flag: str, lock_name: str, resource_name: Optional[str] = None, conflicting_intent: Optional[str] = None) -> tuple[Optional[dict], Optional[str]]:
    '''
    Consult cache and perform a check on a given resource to try to validate the request thr0ugh cache and minimize DB lookups. Although this cannot guarantee resource validity, if it is found to be invalid through cache, an appropriate HTTP exception is raised. If all checks pass, then the resource_mapping (if found) and the latest intent (if found) are returned
    Args:
        client: Redis client instance connected to cache server
        identifier: Unique identifier for resource (Typically PK)
        cache_key: cache key for this resource
        deletion_intent_flag: Deletion flag name for this resource
        action_flag: Flag name to check for latest user intent for this resource
        lock_name: Name of lock for this action
        conflicting_intent: If specified, latest user intent is checked against this value for early rejection

    Returns:
        resource_mapping (dict), latest_intent (str)
    '''
    with client.pipeline() as pipe:
        # Post
        pipe.hgetall(cache_key)
        pipe.get(deletion_intent_flag)  # Existence alone is enough to prove deletion intent
        
        pipe.get(action_flag)   # Get latest intent (if any) for this action
        pipe.get(lock_name)

        resource_mapping, deletion_intent, latest_intent, lock = pipe.execute()

    if deletion_intent:    # Deletion written in cache
        raise Gone('This post has been permanently deleted, and will soon be unavailable')
    if resource_mapping and RedisConfig.NF_SENTINEL_KEY in resource_mapping:    # Non-existence written in cache
        hset_with_ttl(client, cache_key, resource_mapping, RedisConfig.TTL_EPHEMERAL)  # Reannounce non-existence
        raise NotFound(f'No {resource_name} with ID {identifier} could be found (Never existed, or deleted)')
    if lock:    # Stop race condition early
        raise Conflict('Another worker is processing this exact request at the moment')
    if (latest_intent and not conflicting_intent) or (conflicting_intent and latest_intent == conflicting_intent):  # Stop duplicate requests
        raise Conflict(f'This action for resource {identifier} has already been requested')
    
    return resource_mapping, latest_intent  # post_mapping and latest intent may be required by the endpoint later