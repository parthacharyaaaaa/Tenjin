'''Helper functions'''
import datetime
import hashlib
import re
from flask import jsonify
import os
import traceback
from typing import Mapping, Callable, Literal
from types import NoneType
import base64
import requests
import ecdsa
import ujson
from redis import Redis
from redis.exceptions import RedisError

EMAIL_REGEX = r"^(?=.{1,320}$)([a-zA-Z0-9!#$%&'*+/=?^_`{|}~.-]{1,64})@([a-zA-Z0-9.-]{1,255}\.[a-zA-Z]{2,16})$"     # RFC approved babyyyyy

def generic_error_handler(e : Exception):
    '''Return a JSON formatted error message to the client
    
    Contents of the error message are determined by the following:
    - e.message: Error message
    - e.kwargs: Additonal information about the error, attached to HTTP body
    - e.header_kwargs: Additional information (e.g. server's state, broader context of the error message), attached in HTTP headers

    All of these attributes are dictionaries and are **optional**, since in their absense a generic HTTP 500 code is thrown
    '''
    print(traceback.format_exc())
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

def update_jwks(endpoint: str, currentMapping: dict[str, str|int], timeout: int = 3) -> dict[str, str|int]:
    '''Function to fetch JWKS from auth server and load any new key mappings into currentMapping'''
    try:
        response: requests.Response = requests.get(endpoint, timeout=timeout)
        if response.status_code != 200:
            return currentMapping
        
        newMapping: dict[str, str|int] = response.json().get('keys')
        if not newMapping:
            #TODO: Ping auth server to indicate malformatted JWKS response
            return currentMapping
    
        # For any new items in newMapping, we'll need to construct a new dict entry with its public verificiation key
        for keyMetadata in newMapping:
            # New key found, welcome to the club >:3
            if keyMetadata['kid'] not in currentMapping:
                x = from_base64url(keyMetadata['x'])
                y = from_base64url(keyMetadata['y'])
                point = ecdsa.ellipticcurve.Point(ecdsa.SECP256k1.curve, x, y)
                vk = ecdsa.VerifyingKey.from_public_point(point, curve=ecdsa.SECP256k1)

                currentMapping[keyMetadata['kid']] = vk.to_pem()
        return currentMapping
    except:
        return currentMapping


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
        if kwargs.get("username"):
            username : str = kwargs['username'].strip()
            if not (5 < len(username) < 64):
                return False, {"error" : "username must not end or begin with whitespaces, and must be between 5 and 64 characters long"}
            if not username.isalnum():
                return False, {"error" : "username must be strictly alphanumeric"}
        
        if kwargs.get("email"):
            email : str = kwargs['email'].strip()
            if not re.match(EMAIL_REGEX, email, re.IGNORECASE):
                return False, {"error" : "invalid email address"}
        
        if kwargs.get('password'):
            if not (8 < len(kwargs.get('password')) < 64):
                return False, {"error" : "Password length must lie between 8 and 64"}
        
        return True, {"username" : username, "email" : email, "password" : kwargs.get('password')}
    except:
        return False, {"error" : "Malformatted data, please validate data types of each field"}
    
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
            return None
        
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