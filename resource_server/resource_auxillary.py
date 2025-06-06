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
from sqlalchemy import text
from flask_sqlalchemy import SQLAlchemy

EMAIL_REGEX = r"^(?=.{1,320}$)([a-zA-Z0-9!#$%&'*+/=?^_`{|}~.-]{1,64})@([a-zA-Z0-9.-]{1,255}\.[a-zA-Z]{2,16})$"     # RFC approved babyyyyy

def poll_global_key_mapping(interface: Redis) -> dict[str, bytes]:
    '''Poll "JWKS_MAPPING" hashmap in Redis for new key mapping'''
    res: dict[str, str] = interface.hgetall('JWKS_MAPPING')
    if not res:
        raise RuntimeError('JWKS Mapping not found/ found but empty')
    
    return {kid : pub_pem.encode() for kid, pub_pem in res.items()}  # interface has decoded responses, but PyJWT needs public pem in bytes


def update_jwks(endpoint: str, currentMapping: dict[str, bytes], interface: Redis, current_app: Flask, timeout: int = 3, max_global_mapping_polls: int = 10) -> dict[str, str|int]:
    '''Fetch JWKS from auth server and load any new key mappings into currentMapping'''
    res: int = interface.set('JWKS_POLL_LOCK', 1, ex=current_app.config['ANNOUNCEMENT_DURATION'], nx=True)
    if not res:
        # Another worker is currently polling JWKS, wait until lock is released and then read global key mapping
        while(interface.get('JWKS_POLL_LOCK') and max_global_mapping_polls):
            print(f'[JWKS POLLER] Standing by for master thread to perform updation')
            time.sleep(timeout*2)
            max_global_mapping_polls-=1 # Ideally, the lock would always be released no matter what, but a fallback to stop the thread from waiting forever wouldn't hurt
        
        global_mapping: dict[str, str] = poll_global_key_mapping(interface=interface) 
        
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
        with interface.pipeline() as pipe:
            # Overwrite mapping entirely
            pipe.delete('JWKS_MAPPING')
            pipe.hset('JWKS_MAPPING', mapping=currentMapping)

            # Finally release lock and update cooldown
            pipe.delete('JWKS_POLL_LOCK')
            pipe.set('JWKS_POLL_COOLDOWN', current_app.config['JWKS_POLL_COOLDOWN'])
            pipe.execute()

        return currentMapping
    except Exception:
        with interface.pipeline() as pipe:
            pipe.delete('JWKS_POLL_LOCK')
            pipe.set('JWKS_POLL_COOLDOWN', current_app.config['JWKS_POLL_COOLDOWN'])
            pipe.execute()
        print(format_exc())
        return currentMapping

def background_poll(current_app: Flask, interface: Redis, interval: int = 300):
    def run():
        while True:
            try:
                update_jwks(endpoint=f'{current_app.config["AUTH_SERVER_URL"]}/auth/jwks.json',
                            currentMapping= current_app.config['KEY_VK_MAPPING'],
                            current_app=current_app,
                            interface=interface)
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

def write_action_state(interface: Redis, flag_name: str, flag_state: str, flag_ttl: int = 86400, lock_ttl: int = 5) -> None:
    '''
    Write a given state for a pending action.
    Args:
        interface: Redis instance connected to the cachr server
        flag_name: Name of flag to write action state into
        flag_stat: Action state to write
        flag_ttl: Optional TTL in seconds for flag, defaults to 1 day
        lock_ttl: Optional TTL in seconds for lock used when updating state, defaults to 5 seconds

    Returns:
        True if state was succesfully written, False otherwise  
    '''
    lock_name: str = f'lock:{flag_state}L{flag_name}'
    acquired = interface.set(lock_name, value=1, nx=True, ex=lock_ttl)
    if not acquired:
        return False
    
    # Lock acquired
    with interface.pipeline() as pipe:
        pipe.set(flag_name, flag_state, ex=flag_ttl)
        pipe.delete(lock_name)
        pipe.execute()
    return True

def cache_layer_integrity_check(interface: Redis, mapping_key: str, flag_key: str, action_state: str, nf_sentinel_key: str = '__NF__', nf_sentinel_value: str = '-1') -> tuple[bool, bool]:
    '''
    Consult cache to perform an early check on whether a given resource has been deleted, and check if a given state is already the latest state for an action. This consumes only a single network call
    Args:
        interface: Redis instance connected to cache server
        mapping_key: Key name for the cached resource
        flag_key: Key name for the action flag for the action
        action_state: State of action to check against current state (if any)
        nf_sentinel_key: Optional hashmap key for nf sentinel mapping
        nf_sentinel_value: Optional hashmap value for nf sentinel mapping
    Returns:
        tuple of 2 boolean values. First one indicates whether resource does not exist, second one indicates whether current action state matches the requested action state

    '''
    # Check for 404 sentinal value and this action's state with a single network call
    with interface.pipeline(transaction=False) as pipe:
        pipe.hget(mapping_key, nf_sentinel_key)
        pipe.get(flag_key)
        nf_sentinel, current_action_state = pipe.execute()
    return nf_sentinel == nf_sentinel_value, current_action_state == action_state