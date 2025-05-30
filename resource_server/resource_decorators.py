'''Decorators exclusive to resource server'''
from functools import wraps
from flask import request, current_app, g
from werkzeug.exceptions import Unauthorized
from jwt import get_unverified_header, decode
from jwt.exceptions import PyJWTError, ExpiredSignatureError
from datetime import timedelta
from resource_server.resource_auxillary import poll_global_key_mapping
from resource_server.external_extensions import RedisInterface

def token_required(endpoint):
    '''
    Protect an endpoint by validating an access token. Requires the header "Authorization: Bearer <credentials>". 
    Furthermore, sets global data (flask.g.DECODED_TOKEN : _AppCtxGlobals) for usage of token details in the decorated endpoint

    Uses flask.current_app.config['key_mapping'] to check for valid verification keys. On failure, pings the JWKS endpoint to check for the key's existence. If found, updates the mapping with the new key and then performs verification
    '''
    @wraps(endpoint)
    def decorated(*args, **kwargs):
        encodedAccessToken = request.cookies.get("access", request.cookies.get("Access"))
        if not encodedAccessToken:
            raise Unauthorized("Authentication details missing")
        
        headers: dict[str, str|int] = get_unverified_header(encodedAccessToken)
        tokenKID, alg = headers.get('kid'), headers.get('alg')

        # Early exit on visibly invaalid tokens
        if not tokenKID:
            raise Unauthorized("Invalid token, key ID missing")
        
        if alg != 'ES256':
            raise Unauthorized("Invalid token, unsupported algorithm claim")
        
        try: 
            decodedToken: dict = None
            if tokenKID in current_app.config['KEY_VK_MAPPING']:
                decodedToken: dict[str, str|int] = decode(jwt=encodedAccessToken,
                                                          key=current_app.config['KEY_VK_MAPPING'][tokenKID],
                                                          algorithms=["ES256"],
                                                          leeway=timedelta(minutes=3))
                
            # Possibly new KID, ping auth server
            else:
                # Update current mapping through global JWKS mapping
                new_mapping: dict[str, bytes] = poll_global_key_mapping(RedisInterface)                

                if tokenKID not in current_app.config['KEY_VK_MAPPING']:
                    raise Unauthorized('Invalid Key ID, no such key was found. Please login again')
                
                decodedToken: dict[str, str|int] = decode(jwt=encodedAccessToken,
                                                          key=current_app.config['KEY_VK_MAPPING'][tokenKID],
                                                          algorithms=['ES256'],
                                                          leeway=timedelta(minutes=3))

            g.DECODED_TOKEN = decodedToken
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
        tokenKID, alg = headers.get('kid'), headers.get('alg')

        # Early exit on visibly invalid tokens
        if not tokenKID:
            return endpoint(*args, **kwargs)
        
        if alg != 'ES256':
            return endpoint(*args, **kwargs)

        try: 
            if tokenKID in current_app.config['KEY_VK_MAPPING']:
                decodedToken: dict[str, str|int] = decode(jwt=encodedAccessToken,
                                                          key=current_app.config['KEY_VK_MAPPING'][tokenKID],
                                                          algorithms=["ES256"],
                                                          leeway=timedelta(minutes=3))
                
            # Possibly new KID, request auth server
            else:
                # Update current mapping with any new keys fetched from auth server. Failure will return the same mapping itself
                current_app.config['KEY_VK_MAPPING'] = poll_global_key_mapping(RedisInterface)
                if tokenKID not in current_app.config['KEY_VK_MAPPING']:
                    return endpoint(*args, **kwargs)
                
                decodedToken: dict[str: str|int] = decode(jwt=encodedAccessToken,
                                                          key=current_app.config['KEY_VK_MAPPING'][tokenKID],
                                                          algorithms=['ES256'],
                                                          leeway=timedelta(minutes=3))
           
            g.REQUESTING_USER = decodedToken

        except Exception:
            return endpoint(*args, **kwargs)
        
        return endpoint(*args, **kwargs)
    return decorated
