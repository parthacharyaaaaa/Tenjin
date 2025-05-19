from functools import wraps
import ecdsa.ellipticcurve
from flask import request, current_app, g
from werkzeug.exceptions import BadRequest, Unauthorized, InternalServerError
from datetime import timedelta
from jwt import decode, get_unverified_header
from jwt.exceptions import PyJWTError, ExpiredSignatureError
import requests
import base64
import ecdsa

def enforce_json(endpoint):
    @wraps(endpoint)
    def decorated(*args, **kwargs):
        if not request.mimetype or request.mimetype.split("/")[-1] != "json":
            raise BadRequest("Invalid mimetype in request headers (Content-Type), must be */json")
        g.REQUEST_JSON = request.get_json(force=True, silent=True)
        if not g.REQUEST_JSON:
            badReq = BadRequest("Request body non-json parsable")
            badReq.__setattr__("details", "Please ensure that request body is formatted as JSON and does not contain any syntax errors or invalid data types")
            raise badReq
        return endpoint(*args, **kwargs)
    return decorated

def require_intraservice_key(endpoint):
    @wraps(endpoint)
    def decorated(*args, **kwargs):
        key : str = request.headers.get("X-INTERSERVICE-KEY")
        if not key:
            raise Unauthorized("Access Denied: missing interservice key")
        if key not in current_app.config["VALID_API_KEYS"]:
            raise Unauthorized("Access Denied: invalid intraservice key")
        
        return endpoint(*args, **kwargs)
    return decorated

def token_required(endpoint):
    '''
    Protect an endpoint by validating an access token. Requires the header "Authorization: Bearer <credentials>". 
    Furthermore, sets global data (flask.g.decodedToken : _AppCtxGlobals) for usage of token details in the decorated endpoint

    Uses flask.current_app.config['key_mapping'] to check for valid verification keys. On failure, pings the JWKS endpoint to check for the key's existence. If found, updates the mapping with the new key and then performs verification
    '''
    @wraps(endpoint)
    def decorated(*args, **kwargs):
        encodedAccessToken = request.cookies.get("access", request.cookies.get("Access"))
        if not encodedAccessToken:
            raise Unauthorized("Authentication details missing")
        
        headers: dict[str, str|int] = get_unverified_header(encodedAccessToken)
        tokenKID, alg = headers.get('KID'), headers.get('alg')
        if not tokenKID:
            raise Unauthorized("Invalid token, key ID missing")
        
        if alg != 'ES256':
            raise Unauthorized("Invalid token, unsupported algorithm claim")
        
        try: 
            decodedToken: dict = None
            if tokenKID in current_app.config['KNOWN_KEYS']:
                decodedToken = decode(jwt=encodedAccessToken,
                                      key=current_app.config['KNOWN_KEYS'][tokenKID],
                                      algorithms=["ES256"],
                                      leeway=timedelta(minutes=3))
                
            # Possibly new KID, ping auth server
            else:
                response = requests.get(f'{current_app.config["AUTH_SERVER_URL"]}/jwks.json', timeout=3)
                if response.status_code != 200:
                    raise Unauthorized("Failed to validate JWT. This may be an issue with our authentication service")
                
                newMapping: dict[str, str|int] = response.json()['keys']
                # For any new items in newMapping, we'll need to construct a new dict entry with its public verificiation key
                for keyMetadata in newMapping:
                    # New key found, welcome to the club >:3
                    if keyMetadata['kid'] not in current_app.config['KNOWN_KEYS']:
                        x = int.from_bytes(base64.urlsafe_b64decode(keyMetadata['x']).decode(), 'big', True)
                        y = int.from_bytes(base64.urlsafe_b64decode(keyMetadata['y']), 'big', True)
                        point = ecdsa.ellipticcurve.Point(ecdsa.SECP256k1.curve, x, y)
                        vk = ecdsa.VerifyingKey.from_public_point(point, curve=ecdsa.SECP256k1)

                        current_app.config['KNOWN_KEYS'][keyMetadata['kid']] = vk.to_pem()

                if tokenKID not in current_app.config['KNOWN_KEYS']:
                    raise Unauthorized('Invalid Key ID, no such key was found. Please login again')
                
                decodedToken = decode(jwt=encodedAccessToken,
                                      key=current_app.config['KNOWN_KEYS'][tokenKID],
                                      algorithms=['ES256'],
                                      leeway=timedelta(minutes=3))

            g.decodedToken = decodedToken
        except ExpiredSignatureError:
            raise Unauthorized("JWT token expired, begin refresh issuance")
        except PyJWTError as e:
            raise Unauthorized("JWT token invalid")
        
        return endpoint(*args, **kwargs)
    return decorated

def pass_user_details(endpoint):
    '''
    Pass user details by parssing an access token. Requires the header "Authorization: Bearer <credentials>". 
    Furthermore, sets global data (flask.g.REQUESTING_USER : _AppCtxGlobals) for usage of token details in the decorated endpoint
    In case of any errors, fall backs to None
    '''
    #NOTE: Perhaps use Flask.g to include any authentication errors too?
    @wraps(endpoint)
    def decorated(*args, **kwargs):
        g.REQUESTING_USER = None
        encodedAccessToken = request.cookies.get("access", request.cookies.get("Access"))
        if not encodedAccessToken:
            return endpoint(*args, **kwargs)
        
        headers: dict[str, str|int] = get_unverified_header(encodedAccessToken)
        tokenKID, alg = headers.get('KID'), headers.get('alg')
        if not tokenKID:
            return endpoint(*args, **kwargs)
        
        if alg != 'ES256':
            return endpoint(*args, **kwargs)
        
        try: 
            if tokenKID in current_app.config['KNOWN_KEYS']:
                decode(jwt=encodedAccessToken,
                                      key=current_app.config['KNOWN_KEYS'][tokenKID],
                                      algorithms=["ES256"],
                                      leeway=timedelta(minutes=3))
                
            # Possibly new KID, ping auth server
            else:
                response = requests.get(f'{current_app.config["AUTH_SERVER_URL"]}/jwks.json', timeout=3)
                if response.status_code != 200:
                    return endpoint(*args, **kwargs)
                
                newMapping: dict[str, str|int] = response.json()['keys']
                # For any new items in newMapping, we'll need to construct a new dict entry with its public verificiation key
                for keyMetadata in newMapping:
                    # New key found, welcome to the club >:3
                    if keyMetadata['kid'] not in current_app.config['KNOWN_KEYS']:
                        x = int.from_bytes(base64.urlsafe_b64decode(keyMetadata['x']).decode(), 'big', True)
                        y = int.from_bytes(base64.urlsafe_b64decode(keyMetadata['y']), 'big', True)
                        point = ecdsa.ellipticcurve.Point(ecdsa.SECP256k1.curve, x, y)
                        vk = ecdsa.VerifyingKey.from_public_point(point, curve=ecdsa.SECP256k1)

                        current_app.config['KNOWN_KEYS'][keyMetadata['kid']] = vk.to_pem()

                if tokenKID not in current_app.config['KNOWN_KEYS']:
                    return endpoint(*args, **kwargs)
                
                decode(jwt=encodedAccessToken,
                                      key=current_app.config['KNOWN_KEYS'][tokenKID],
                                      algorithms=['ES256'],
                                      leeway=timedelta(minutes=3))

        except Exception:
            return endpoint(*args, **kwargs)
        
        return endpoint(*args, **kwargs)
    return decorated

def private(endpoint):
    @wraps(endpoint)
    def decorated(*args, **kwargs):
        try:
            # IP check
            request_ip = request.headers.get("X-FORWARDED-FOR", request.remote_addr)
            if not (request_ip in current_app.config["PRIVATE_IP_ADDRS"]):
                raise Unauthorized()
            
            # HTTP check
            if not request.headers["PRIVATE-API-KEY"] in current_app.config["PRIVATE_COMM_KEYS"]:
               raise Unauthorized("Access Denied >:(")
            
        except KeyError:
               if not request.headers.get("PRIVATE-API-KEY"):
                   raise Unauthorized("Private endpoint, requires an API key")
               else:
                   raise InternalServerError()
        return endpoint(*args, **kwargs)
    return decorated
