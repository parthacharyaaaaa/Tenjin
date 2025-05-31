'''Auxillary functions exclusive to the resource server'''
import requests
import re
from auxillary.utils import from_base64url
import ecdsa
from redis import Redis
from flask import Flask
import time
from traceback import format_exc
import threading
import os

EMAIL_REGEX = r"^(?=.{1,320}$)([a-zA-Z0-9!#$%&'*+/=?^_`{|}~.-]{1,64})@([a-zA-Z0-9.-]{1,255}\.[a-zA-Z]{2,16})$"     # RFC approved babyyyyy

def poll_global_key_mapping(RedisInterface: Redis) -> dict[str, bytes]:
    '''Poll "JWKS_MAPPING" hashmap in Redis for new key mapping'''
    res: dict[str, str] = RedisInterface.hgetall('JWKS_MAPPING')
    if not res:
        raise RuntimeError('JWKS Mapping not found/ found but empty')
    
    return {kid : pub_pem.encode() for kid, pub_pem in res.items()}  # RedisInterface has decoded responses, but PyJWT needs public pem in bytes


def update_jwks(endpoint: str, currentMapping: dict[str, bytes], RedisInterface: Redis, current_app: Flask, timeout: int = 3, max_global_mapping_polls: int = 10) -> dict[str, str|int]:
    '''Fetch JWKS from auth server and load any new key mappings into currentMapping'''
    res: int = RedisInterface.set('JWKS_POLL_LOCK', 1, ex=current_app.config['ANNOUNCEMENT_DURATION'], nx=True)
    if not res:
        # Another worker is currently polling JWKS, wait until lock is released and then read global key mapping
        while(RedisInterface.get('JWKS_POLL_LOCK') and max_global_mapping_polls):
            print(f'[JWKS POLLER] Standing by for master thread to perform updation')
            time.sleep(timeout*2)
            max_global_mapping_polls-=1 # Ideally, the lock would always be released no matter what, but a fallback to stop the thread from waiting forever wouldn't hurt
        
        global_mapping: dict[str, str] = poll_global_key_mapping(RedisInterface=RedisInterface) 
        
        return {kid:pub_pem.encode() for kid, pub_pem in global_mapping}
    
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
        with RedisInterface.pipeline() as pipe:
            # Overwrite mapping entirely
            pipe.delete('JWKS_MAPPING')
            pipe.hset('JWKS_MAPPING', mapping=currentMapping)

            # Finally release lock and update cooldown
            pipe.delete('JWKS_POLL_LOCK')
            pipe.set('JWKS_POLL_COOLDOWN', current_app.config['JWKS_POLL_COOLDOWN'])
            pipe.execute()

        return currentMapping
    except Exception:
        with RedisInterface.pipeline() as pipe:
            pipe.delete('JWKS_POLL_LOCK')
            pipe.set('JWKS_POLL_COOLDOWN', current_app.config['JWKS_POLL_COOLDOWN'])
            pipe.execute()
        print(format_exc())
        return currentMapping

def background_poll(current_app: Flask, RedisInterface: Redis, interval: int = 300):
    def run():
        while True:
            try:
                update_jwks(endpoint=f'{current_app.config["AUTH_SERVER_URL"]}/auth/jwks.json',
                            currentMapping= current_app.config['KEY_VK_MAPPING'],
                            current_app=current_app,
                            RedisInterface=RedisInterface)
            except Exception:
                print(f"[JWKS POLLER]: Error: {format_exc()}")
            finally:
                time.sleep(interval)
    background_poll_thread: threading.Thread = threading.Thread(target=run, daemon=True)
    background_poll_thread.run()

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
  