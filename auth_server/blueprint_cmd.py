from flask import Blueprint, current_app, jsonify, g
from auth_server.redis_manager import SyncedStore
from auxillary.decorators import enforce_json
from auxillary.utils import genericDBFetchException, verify_password, hash_password
from auth_server.auth_auxillary import report_suspicious_activity, admin_only
from werkzeug.exceptions import BadRequest, InternalServerError, NotFound, Forbidden, Conflict, Unauthorized
from auth_server.models import db, Admin, SuspiciousActivity
import secrets
import time
from werkzeug import Response
from datetime import datetime
import base64
import ujson

from sqlalchemy import select, update, insert
from sqlalchemy.exc import SQLAlchemyError

from redis.exceptions import RedisError
cmd: Blueprint = Blueprint('cmd', 'cmd', url_prefix='/cmd')

@cmd.route('/admins/login', methods=['POST'])
@enforce_json
def admin_login() -> tuple[Response, int]:
    identity: str = g.REQUEST_JSON.pop('identity', '').strip()
    password: str = g.REQUEST_JSON.pop('password', None)

    if not (identity and password):
        raise BadRequest('Password and identity missing in JSON')

    try:
        admin: Admin = db.session.execute(select(Admin)
                                          .where((Admin.username == identity) & (Admin.time_deleted == None))
                                          ).scalar_one_or_none()
        
        if not admin:
            raise NotFound("No admin with these credentials found")
        
        if admin.locked:
            report_suspicious_activity(admin.id, 'Attempt to log into a locked account', force_logout=False)
            raise Forbidden('This account is currently locked on grounds of suspicious activities')
    except SQLAlchemyError: genericDBFetchException()
    
    if not verify_password(password, admin.password_hash, admin.password_salt):
        report_suspicious_activity(admin.id, 'Incorrect password')
        raise Unauthorized('Incorrect passwword')

    # Exists in DB, check SyncedStore to see if session is already active
    sessionKey: str = f'admin:{admin.id}'
    try:
        adminSession: dict = SyncedStore.hgetall(sessionKey)

        # Single sign-in policy, invalidate existing session and add entry in logs
        if adminSession:
            SyncedStore.delete(sessionKey)
            report_suspicious_activity(admin.id, 'Session already active')
            raise Conflict('An admin session with these credentials is already active')

    except RedisError:
        raise InternalServerError('An error occured when validating session integrity')
    
    try:
        db.session.execute(update(Admin).values(last_login=datetime.now()))
    except SQLAlchemyError: raise InternalServerError('An error occured when logging you in, this is not an issue with your request but with the database')
    
    # Admin validated, create new session
    sessionID: int = secrets.randbelow(10_000_000)
    revivalDigest: str = secrets.token_hex(256)
    epoch: float = time.time()
    expiry: float = epoch + current_app.config['ADMIN_SESSION_DURATION']
    sessionMapping: dict = {'admin_id' : admin.id,
                          'session_id' : sessionID,
                          'revival_digest' : revivalDigest,
                          'epoch' : epoch,
                          'expiry_at' : expiry,
                          'role' : admin.role}

    SyncedStore.hset(sessionKey, mapping=sessionMapping)        # Set hashmap with private revival digest
    sessionMapping.pop('revival_digest')
    sessionMapping['message'] = 'Login successful'

    encodedSessionToken: str = base64.urlsafe_b64encode(ujson.dumps(sessionMapping).encode()).decode()
    return jsonify({'session_token' : encodedSessionToken}), 200
    
@cmd.route('/admins', methods=['DELETE'])
def admin_delete() -> tuple[Response, int]:
    ...

@cmd.route('/admins/logout', methods=['PATCH'])
@admin_only
def admin_logout() -> tuple[Response, int]:
    SyncedStore.delete(f'admin:{g.SESSION_TOKEN["admin_id"]}')
    return jsonify({'message' : 'Logout successful'}), 200

@cmd.route('/admins', methods=['POST'])
@enforce_json
@admin_only
def create_admin() -> tuple[Response, int]:
    if g.SESSION_TOKEN.get('role') != 'super':
        raise Unauthorized('Only super roles are allowed to add admins')
    
    username: str = g.REQUEST_JSON.get('username', '').strip()
    password: str = g.REQUEST_JSON.get('password')
    if not (username and password):
        raise BadRequest("Password and username are required to create a new admin")
    
    try:
        existingAdmin : Admin = db.session.execute(select(Admin).where(Admin.username == username)).scalar_one_or_none()
        if existingAdmin:
            raise Conflict('An admin with this suername already exists')
    except SQLAlchemyError: genericDBFetchException()
    
    pw_hash, pw_salt = hash_password(password)
    try:
        db.session.execute(insert(Admin).values(username=username,
                                                password_hash = pw_hash, password_salt = pw_salt,
                                                role='staff',
                                                created_by=g.SESSION_TOKEN['admin_id']))
        db.session.commit()
    except SQLAlchemyError:
        raise InternalServerError("Failed to create a new admin, this is not from an erroneous input")
    
    return jsonify({'message' : 'Admin created'}), 202

@cmd.route('/keys/purge', methods=['DELETE'])
@enforce_json
def purge_keys() -> tuple[Response, int]:
    '''Purge all keys from the key store, including the most recent one'''
    ...

@cmd.route('/keys/purge/<int:kid>', methods=['DELETE'])
def purge_key(kid: int) -> tuple[Response, int]:
    ...

@cmd.route('/keys/clean', methods=['DELETE'])
def clean_keystore() -> tuple[Response, int]:
    '''Purge all keys except the most recent one'''
    ...

@cmd.route('/keys/add', methods=['POST'])
@enforce_json
def add_key() -> tuple[Response, int]:
    ...

@cmd.route('/keys/rotate', methods=['POST'])
@enforce_json
def rotate_keys() -> tuple[Response, int]:
    '''Manually trigger a key rotation sequence, resetting the timer on success'''
    ...

@cmd.route('/keys/capacity', methods=['PATCH'])
@enforce_json
def cap_keys() -> tuple[Response, int]:
    '''Enforce max capacity on keys to be stored'''
    ...