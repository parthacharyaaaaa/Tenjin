'''Helper functions'''
import datetime
import hashlib
from flask import jsonify
import os
import traceback
from typing import Mapping, Callable, Literal, Any, Iterable
from types import NoneType
import base64
import ujson
from redis import Redis
from redis.client import Pipeline
from redis.exceptions import RedisError

def generic_error_handler(e : Exception):
    '''Return a JSON formatted error message to the client
    
    Contents of the error message are determined by the following:
    - e.message: Error message
    - e.kwargs: Additonal information about the error, attached to HTTP body
    - e.header_kwargs: Additional information (e.g. server's state, broader context of the error message), attached in HTTP headers

    All of these attributes are dictionaries and are **optional**, since in their absense a generic HTTP 500 code is thrown
    '''
    response = jsonify({"message" : getattr(e, "description", "An error occured"),
                        **getattr(e, "kwargs", {})})
    if getattr(e, "header_kwargs", None):
        response.headers.update(e.header_kwargs)

    return response, getattr(e, "code", 500)

def to_base64url(n: int, length: int = 32) -> str:
    return base64.urlsafe_b64encode(n.to_bytes(length, byteorder='big')).rstrip(b'=').decode('utf-8')

def from_base64url(b64url: str) -> int:
    # Add back padding if needed
    padding = '=' * ((4 - len(b64url) % 4) % 4)
    padded_b64url = b64url + padding
    byte_data = base64.urlsafe_b64decode(padded_b64url)
    return int.from_bytes(byte_data, byteorder='big')


def hash_password(password: str, salt: bytes = None) -> tuple[bytes, bytes]:
    '''
    Produce a password salt and hash from a given string
    
    returns: tuple[password-hash, salt]'''
    if salt is None:
        salt = os.urandom(16)
    passwordHash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
    return passwordHash, salt

def verify_password(password: str, password_hash : bytes, salt: bytes) -> bool:
    '''
    Match a given password and salt with a hashed password
    '''
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000) == password_hash
  
def rediserialize(mapping: dict, 
                  typeMapping: Mapping[type, Callable] = {NoneType : lambda _ : '',
                                                          bool: lambda b : int(b), 
                                                          datetime.datetime: lambda dt : dt.isoformat()}) -> dict:
    '''Serialize a Python dictionary to a Redis hashmap'''
    return {k : typeMapping.get(type(v), lambda x : x)(v) for k,v in mapping.items()}

def genericDBFetchException():
    '''Generic fetch exception handler'''
    exc = Exception()
    exc.__setattr__("description", 'An error occurred when fetching this resource')
    raise exc

def consult_cache(interface: Redis, cache_key: str,
                  ttl_cap: int, ttl_promotion: int = 15, ttl_ephemeral: int = 15, 
                  dtype: Literal['mapping', 'string'] = 'mapping', nf_repr: str = '__NF__', suppress_errors: bool = True) -> dict|None:
    '''
    Consult Redis cache and attempt to fetch the given key.
    Args:
        interface: Redis instance connected to cache server
        cache_key: Name of the key to search for
        ttl_cap: Maximum TTL in seconds for any entry in cache
        ttl_promotion: Seconds to add to an existing entry's TTL on cache hit
        ttl_ephemeral: TTL in seconds for ephemeral announcements
        dtype: Redis data structure assosciated with this item. Defaults to hashmap
        nf_repr: Representation of a key that does not exist. If dtype is mapping, then it is interpreted as {nf_val:-1}
        suppress_errors: Flag to allow silent failures. Ideally this should be set to True to allow graceful fallback to database

    Returns
        {"__NF__" : True} if nf_repr found, None on cache miss/suppressed failure, and cached mapping on cache hits
    '''
    res: list[dict|str, int] = [None]
    try:
        with interface.pipeline(transaction=False) as pipe:
            if dtype == 'mapping':
                pipe.hgetall(cache_key)
            else:
                pipe.get(cache_key)
            pipe.ttl(cache_key)
            res = pipe.execute()

        if not res[0]:  # Cache miss
            return None
        
        if nf_repr in res[0] or res[0] == nf_repr:
            # cache_key is guaranteed to not exist anywhere, reannounce non-existence of key and then return None
            if dtype == 'mapping':
                with interface.pipeline() as pipe:
                    pipe.hset(cache_key, mapping={nf_repr:-1})
                    pipe.expire(cache_key, ttl_ephemeral)
                    pipe.execute()
            else:
                interface.set(cache_key, nf_repr, ttl_ephemeral)
            return {'__NF__':True}
        
        # Cache hit, and resource actually exists
        cachedResource: dict = res[0] if dtype == 'mapping' else ujson.loads(res[0])
        cachedTTL: int = res[1]

        interface.expire(cache_key, min(ttl_cap, ttl_promotion+cachedTTL))
        return cachedResource

    except RedisError as e:
        if suppress_errors:
            return None
        else:
            raise RuntimeError('Unsuppressed cache failure') from e
        
def fetch_group_resources(interface: Redis, group_key: str, element_dtype: Literal['mapping', 'string'] = 'mapping') -> tuple[tuple[Any], bool, str]:
    """
    Fetches all values for keys stored in a Redis iterable (list, set, or sorted set) for cursor based pagination. If any key is missing from the cache, the function returns `None` to indicate a cache miss. It is upto the caller to reconcile cache misses
    Args:
        interface: The Redis client instance used to access the cache.
        group_key: The Redis key pointing to a collection of resource keys.
        element_dtype: The expected data type of each individual resource key in the group (used for deserialization).

    Returns:
        tuple: A tuple of values corresponding to each key in the group, boolean indicating end of pagination, value of next cursor
    """
    keys: Iterable[str] = None
    keys: list[str] = interface.lrange(group_key, 0, -1)

    if not keys: return None, True, None

    if '__NF__' in keys: return None, True, None

    cursor: str = None
    end: bool = False
    removed_entries: list[int] = []
    for idx, entry in enumerate(keys):
        if entry.startswith('cursor:'): 
            cursor = entry.split(":")[1]    # Fetch next cursor for pagination if available
            removed_entries.append(entry)
        elif entry.startswith("end:"): 
            removed_entries.append(entry)
            end = entry.split(":")[1]        # Fetch flag to indiciate pagination end
    for entry in removed_entries: keys.remove(entry)   # Remove cursor and end keys from group keys

    end = False if end.lower() == 'false' else True      # Cast from Redis string to Python bool
    resources: list[dict[str, Any]|str] = []
    with interface.pipeline() as pipe:
        for key in keys:
            if element_dtype == 'mapping':
                pipe.hgetall(key)
            else:
                pipe.get(key)
        resources = pipe.execute()

    # Account for sentinel mappings
    if element_dtype == 'mapping':
        return tuple(map(lambda resource : None if '__NF__' in resource else resource, resources)), end, cursor
    
    return tuple(map(lambda resource : None if resource == '__NF__' else ujson.loads(resource), resources)), end, cursor

def promote_group_ttl(interface: Redis, group_key: str, promotion_ttl: int = 15, max_ttl: int = 20*60) -> None:
    keys: Iterable[str] = None
    end: bool = False
    keys: list[str] = interface.lrange(group_key, 0, -1)

    if not keys:
        return
    
    # Fetch TTLs
    with interface.pipeline() as pipe:
        pipe.ttl(group_key)
        for key in keys:
            pipe.ttl(key)

        ttl_list: list[int] = pipe.execute()
    
    # Promote TTls
    with interface.pipeline() as pipe:
        pipe.expire(group_key, min(max_ttl, ttl_list[0] + promotion_ttl))

        for idx, key in enumerate(keys, start=1):
            pipe.expire(key, min(max_ttl, ttl_list[idx] + promotion_ttl))
        pipe.execute()

def cache_grouped_resource(interface: Redis, group_key: str, resource_type: str, resources: Mapping[str|int, dict], weak_ttl: int, strong_ttl: int, cursor: str, end: bool, member_dtype: Literal['mapping', 'string'] = 'mapping') -> None:
    member_key_template: str = resource_type+':{}'
    if member_dtype not in ('mapping', 'string'): raise ValueError()

    with interface.pipeline() as pipe:
        pipe.expire(group_key, weak_ttl)
        for resourceID, resourceMapping in resources.items():
            key_name: str = member_key_template.format(resourceID)
            pipe.rpush(group_key, key_name)     # Push key name for resource into list

            # Cache individual resource separately
            if member_dtype == 'mapping':
                pipe.hset(key_name, mapping=resourceMapping)
            else:
                pipe.set(key_name, ujson.dumps(resourceMapping))
            pipe.expire(key_name, strong_ttl)
        
        pipe.rpush(group_key, f'cursor:{cursor}')
        pipe.rpush(group_key, f'end:{end}')
        pipe.execute()