from flask import Blueprint, current_app, jsonify, g
from auth_server.redis_manager import SyncedStore
from auxillary.decorators import enforce_json
from auxillary.utils import genericDBFetchException, verify_password, hash_password
from auth_server.auth_auxillary import report_suspicious_activity, admin_only
from werkzeug.exceptions import BadRequest, InternalServerError, NotFound, Forbidden, Conflict, Unauthorized
from auth_server.models import db, Admin, KeyData
from auth_server.keygen import generate_ecdsa_pair, write_ecdsa_pair, update_jwks
from auth_server.key_container import KeyMetadata
from auth_server.token_manager import tokenManager
import secrets
import time
from werkzeug import Response
from datetime import datetime
import base64
import ujson
import ecdsa
import os

from sqlalchemy import select, update, insert, func
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
@enforce_json
def admin_delete() -> tuple[Response, int]:
    if g.SESSION_TOKEN.get('role') != 'super':
        raise Unauthorized('Only super users are allowed to delete admins')
    
    purgeID: int = g.REQUEST_JSON.get('id')
    if not purgeID:
        raise BadRequest('ID must be provided for deletion')
    
    try:
        admin: Admin = db.session.execute(select(Admin)
                                          .where((Admin.id == purgeID) & (Admin.time_deleted == None))
                                          ).scalar_one_or_none()
        if not admin:
            raise NotFound(f'No admin with ID {purgeID} found')
        
        if admin.role == 'super':
            raise Conflict()
        
    except SQLAlchemyError: genericDBFetchException()

    try:
        db.session.execute(update(Admin)
                        .where(Admin.id == admin.id)
                        .values(time_deleted = datetime.now()))
    except: raise InternalServerError("Failed to delete admin account")

    return jsonify({'message' : 'Admin deleted'}), 200
    

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

@cmd.route('/keys/purge/<int:kid>', methods=['DELETE'])
def purge_key(kid: int) -> tuple[Response, int]:
    ...

@cmd.route('/keys/clean', methods=['DELETE'])
def clean_keystore() -> tuple[Response, int]:
    '''Purge all keys except the most recent one'''
    ...

@cmd.route('/keys/rotate', methods=['POST'])
@admin_only
def rotate_keys() -> tuple[Response, int]:
    '''Trigger a key rotation sequence'''
    # Check for concurrent worker performing a key rotation
    lock = SyncedStore.set('KEY_ROTATION_LOCK', g.SESSION_TOKEN['admin_id'], ex=300, nx=True)
    if not lock:
        # Another worker is performing this action, reject this request >:(
        adminID: str = SyncedStore.get('KEY_ROTATION_LOCK')
        return jsonify({'message' : "There is an active key rotation being performed, your request has been rejected", 'admin_id' : adminID}), 409
    
    # Check for cooldown, must be global for all staff admins
    cooldown_flag: int = SyncedStore.get('KEY_ROTATION_COOLDOWN')
    if cooldown_flag and g.SESSION_TOKEN['role'] == 'staff':
        report_suspicious_activity(adminID=g.SESSION_TOKEN['admin_id'], desc='Attempt to perform key rotation during cooldown')
        raise Conflict('The server is currently undergoing a key rotation cooldown, and will not accept rotation requets. Repeated attempt will lead to account lock')
    
    # Server is ready for a key rotation
    kid, signingKey, verificationKey = generate_ecdsa_pair()

    # Update DB first, then perform JWKS and PEM writes
    overflow: bool = False
    purgeID: int = None
    try:
        # Update currently active key
        prevKID: int = db.session.execute(select(KeyData.kid)
                                          .where(KeyData.rotated_out_at == None)
                                          .with_for_update(nowait=True, read=True)).scalar_one()    # Lock record
        # Reflect rotation in DB
        db.session.execute(update(KeyData)
                           .where(KeyData.kid == prevKID)
                           .values(rotated_out_at=datetime.now(), manual_rotation=True, rotated_out_by=g.SESSION_TOKEN['admin_id']))
        
        # Add new key
        db.session.execute(insert(KeyData)
                           .values(kid=kid, curve=str(ecdsa.SECP256k1), private_pem=signingKey.to_pem(), public_pem=verificationKey.to_pem()))

        # Check whether max capacity has been reached. If so, purge oldest key
        valid_key_count: int = db.session.execute(select(func.count()).select_from(KeyData).where(KeyData.expired_at == None)).scalar_one()
        if valid_key_count >= current_app.config['MAX_VALID_KEYS']:
            overflow=True
            # Select and lock oldest, non-expired valid key
            purgeID: int = db.session.execute(select(KeyData.kid)
                               .where((KeyData.rotated_out_at.isnot(None)) & (KeyData.expired_at == False))
                               .with_for_update(nowait=True)
                               .order_by(KeyData.rotated_out_at.desc())
                               .limit(1)
                               ).scalar_one()
            # Update expired_at column
            db.session.execute(update(KeyData).where(KeyData.kid == purgeID).values(expired_at=datetime.now()))
        db.session.commit()
            
    except SQLAlchemyError:
        db.session.rollback()
        raise InternalServerError('An error occured in performing key rotation (Database level)')

    # Update files
    update_jwks(verificationKey, kid, current_app.config['JWKS_FPATH'], capacity=current_app.config['MAX_VALID_KEYS'])  # Implictly trims old JWKS data, very kewl >:3
    write_ecdsa_pair(privateDir=current_app.config['PRIVATE_PEM_DIRECTORY'], staticDir=current_app.config['PUBLIC_PEM_DIRECTORY'],
                     encryption_key=current_app.config['PRIVATE_PEM_ENCRYPTION_KEY'],
                     private_key=signingKey, public_key=verificationKey, key_id=kid)
    
    # Remove previous key's private PEM file
    os.remove(os.path.join(current_app.config['PRIVATE_PEM_DIRECTORY'], f'private_{prevKID}.pem'))
    if overflow:
        # Delete oldest public PEM file.
        os.remove(os.path.join(current_app.config['PUBLIC_PEM_DIRECTORY'], f'public_{purgeID}.pem'))
        privateFpath: os.PathLike = os.path.join(current_app.config['PRIVATE_PEM_DIRECTORY'], f'private_{purgeID}.pem')

        # Explicit check because ideally at key rotation because normally the private PEM file for any non-active valid key should already have been deleted.
        if os.path.exists(privateFpath):
            os.remove(privateFpath)
    
    # Update token manager's mapping to use this newly created ECDSA pair
    newKeyData: KeyMetadata = KeyMetadata(PUBLIC_PEM=verificationKey.to_pem(), PRIVATE_PEM=signingKey.to_pem(), ALGORITHM='ES256')
    tokenManager.update_keydata(kid, newKeyData)
    
    # Since token manager is now updated with the new key, it will now sign all tokens with the new key data. Any other worker's token manager will update it's mapping whenever it encounters a token with this new KID, so we need not bother with some async timer to ping some announcement channel anyways -_-

    # Set global cooldown for key rotation
    SyncedStore.set('KEY_ROTATION_COOLDOWN', 1, ex=current_app.config['KEY_ROTATION_COOLDOWN'])
    # Release lock
    SyncedStore.delete('KEY_ROTATION_LOCK')

    return jsonify({'message' : 'Key rotation successful', 'kid' : kid , 'public_pem' : newKeyData.PUBLIC_PEM.decode(), 'epoch' : newKeyData.EPOCH, 'alg' : 'ES256', 'previous_kid' : prevKID}), 201
