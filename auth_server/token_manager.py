import jwt
from typing import Optional, Literal
from redis import Redis
from werkzeug.exceptions import InternalServerError
import uuid
import time
from typing import TypeAlias
import jwt.exceptions as JWTexc
from auth_server.key_container import KeyMetadata
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import select
from auth_server.models import KeyData
import threading
from traceback import format_exc

# Aliases
tokenPair : TypeAlias = tuple[str, str]

class TokenManager:
    '''### Class for issuing and verifying access and refresh tokens assosciated with authentication and authorization'''

    activeRefreshTokens : int = 0

    def __init__(self, kvsMapping: dict[str, KeyMetadata],
                 interface: Redis,
                 synced_store: Redis,
                 db: SQLAlchemy,
                 refreshLifetime : int = 60*60*3,
                 accessLifetime : int = 60*30,
                 alg : str = "ES256",
                 typ : str = "JWT",
                 uClaims : dict = {},
                 uHeaders : dict | None = None,
                 leeway : int = 180,
                 max_tokens_per_fid : int = 3,
                 max_valid_keys: int = 3,
                 announcement_duration: int = 60*60*3):
        '''
        Args:
            kvsMapping (dict): Mapping of key IDs to key metadata (see `KeyMetadata` class). Never expose private keys via endpoints.
            interface (Redis): Redis interface instance for caching or blacklisting.
            dbConnString (str): Database URI for persistent storage.
            refreshLifetime (int): Lifetime of refresh tokens (default: 3 hours).
            accessLifetime (int): Lifetime of access tokens (default: 30 minutes).
            alg (str): JWT signing algorithm. Default is "ES256".
            typ (str): Token type, usually "JWT".
            uClaims (dict): Universal claims to include in all tokens.
            uHeaders (dict, optional): Additional JWT headers.
            leeway (int): Leeway time in seconds for token validation. Default is 180s.
            max_tokens_per_fid (int): Max tokens allowed per user/session.
        '''
        self.kvsMapping = kvsMapping
        self.latestKeyID, self.latestKeyMetadata = next(reversed(kvsMapping.items()))

        try:
            self._TokenStore = interface
            self.max_llen = max_tokens_per_fid
        except Exception as e:
            raise ValueError("Mandatory configurations missing for _TokenStore") from e

        if not (self._TokenStore.ping()):
            raise ConnectionError()
        
        self.announcement_duration = announcement_duration
        
        self._SyncedStore = synced_store
        if not (self._SyncedStore.ping()):
            raise ConnectionError()

        self.db = db

        # Initialize universal headers, common to all tokens issued in any context
        uHeader = {"typ" : typ, "alg" : alg}
        if uHeaders:
            uHeader.update(uHeaders)
        self.uHeader = uHeader
        # Initialize universal claims, common to all tokens issued in any context. 
        # These should at the very least contain registered claims like "exp"
        self.uClaims = uClaims

        self.refreshLifetime = refreshLifetime
        self.accessLifetime = accessLifetime

        # Set leeway for time-related claims
        self.leeway = leeway
        self.max_valid_keys = max_valid_keys

        # Start background thread for polling
        threading.Thread(target=self.poll_store, daemon=True).start()

    def decodeToken(self, token : str, tType : Literal["access", "refresh"] = "access", **kwargs) -> str:
        '''Decodes token, raises error in case of failure
        Args:
        token: The token to decode
        tType: Type of token. This is needed to invalidate token families for compromised refresh tokens
        '''
        try:
            kid: int = jwt.get_unverified_header(token)['kid']
            if kid not in self.kvsMapping:
                raise JWTexc.InvalidKeyError('This key is not recognised, meaning it is possibly tampered, forged, or simply expired a long time ago.')
            
            return jwt.decode(jwt = token,
                            key = self.kvsMapping[kid].PUBLIC_PEM,
                            algorithms = [self.kvsMapping[kid].ALGORITHM],
                            leeway = self.leeway,
                            options=kwargs.get('options'))
        except (JWTexc.ImmatureSignatureError, JWTexc.InvalidIssuedAtError, JWTexc.InvalidIssuerError) as e:
            if tType == "refresh":
                self.invalidateFamily(jwt.decode(token, options={"verify_signature":False})["fid"])
            raise ValueError("PP")
        except KeyError as e:
            raise JWTexc.InvalidTokenError('Token headers missing key ID')

    def reissueTokenPair(self, rToken : str) -> tokenPair:
        '''
        Issue a new token pair from a given refresh token, and revoke the provided refresh token
        Args:
        rToken: JWT encoded refresh token'''
        decodedRefreshToken = self.decodeToken(rToken, tType = "refresh")

        # issue tokens here
        refreshToken = self.issueRefreshToken(decodedRefreshToken["sub"],
                                              decodedRefreshToken['sid'],
                                              reissuance=True,
                                              jti=decodedRefreshToken["jti"],
                                              familyID=decodedRefreshToken["fid"],
                                              exp=decodedRefreshToken["exp"])
        
        self.shiftTokenWindow(decodedRefreshToken['fid'])

        accessToken = self.issueAccessToken(decodedRefreshToken['sub'], decodedRefreshToken['sid'],
                                            additionalClaims={"fid" : decodedRefreshToken["fid"]})
        
        return refreshToken, accessToken

    def issueRefreshToken(self, sub: str, sid: int,
                          additionalClaims : Optional[dict] = None,
                          reissuance : bool = False,
                          jti : Optional[str] = None,
                          familyID : Optional[str] = None,
                          exp : Optional[int] = None) -> str:
        '''
        #### Issue a new refresh token
        **Note**: This method will always encrypt the token with the newest available signing key
        
        Args:
        sub: subject of the JWT
        sid: DB ID of subject
        additionalClaims: Additional claims to attach to the JWT body
        reissuance: Whether issuance is assosciated with a new authorization flow or not
        jti: JTI claim of the current refresh token
        familyID: FID claim of the current refresh token

        '''
        if reissuance:
            # Check for replay attack
            key = self._TokenStore.lindex(f"FID:{familyID}", 0)
            if not key:
                self.invalidateFamily(familyID)
                raise ValueError(f"Token family {familyID} is invalid or empty")
            key_metadata = key.split(b":")
            if str(key_metadata[0]) != jti or float(key_metadata[1]) != exp:
                self.invalidateFamily(familyID)
                raise ValueError(f"Replay attack detected or token metadata mismatch for family {familyID}")

        elif self._TokenStore.lrange(f"FID:{familyID}", 0, -1):
            # A new authorization attempt, but the family already exists. For this project, we only allow single logins per user, so just pull an Itachi and ask to login again
            self.invalidateFamily(familyID)
            raise ValueError(f"Token family {familyID} already exists, cannot issue a new token with the same family")

        # All checks passed
        payload: dict = {"iat" : time.time(),
                          "exp" : time.time() + self.refreshLifetime,
                          "nbf" : time.time() + self.accessLifetime - self.leeway,
                          "fid" : familyID,
                          "sub" : sub,
                          "sid" : sid,
                          "jti" : self.generate_unique_identifier()}
        payload.update(self.uClaims)
        if additionalClaims:
            payload.update(additionalClaims)

        with self._TokenStore.pipeline(transaction=False) as pipe:
            pipe.lpush(f"FID:{familyID}", f"{payload['jti']}:{payload['exp']}")
            pipe.expireat(f"FID:{familyID}", int(payload["exp"]))

        return jwt.encode(payload=payload,
                          key=self.latestKeyMetadata.PRIVATE_PEM,
                          algorithm=self.latestKeyMetadata.ALGORITHM,
                          headers=self.uHeader | {'kid' : self.latestKeyID})

    def issueAccessToken(self, sub : str, sid: int, familyID: str, additionalClaims : dict|None = None) -> str:
        payload: dict = {"iat" : time.time(),
                          "exp" : time.time() + self.accessLifetime,
                          "fid" : familyID,
                          "sub" : sub,
                          "sid" : sid,
                          "jti" : self.generate_unique_identifier()}
        payload.update(self.uClaims)
        if additionalClaims:
            payload.update(additionalClaims)

        return jwt.encode(payload=payload,
                          key=self.latestKeyMetadata.PRIVATE_PEM,
                          algorithm=self.latestKeyMetadata.ALGORITHM,
                          headers=self.uHeader | {'kid': self.latestKeyID})

    def shiftTokenWindow(self, fID : str) -> None:
        '''Revokes the oldest refresh token from a family if capacity is reached, without invalidating the entire family'''
        try:
            llen: int = self._TokenStore.llen(f"FID:{fID}")

            if llen == 0:
                return "Key does not exist"
            
            if llen >= self.max_llen:
                self._TokenStore.rpop(f"FID:{fID}", max(1, llen-self.max_llen))
        except Exception as e:
            raise InternalServerError("Failed to perform operation on token store")

    def invalidateFamily(self, fID : str) -> None:
        '''Remove entire token family from revocation list and token store'''
        try:
            if self._TokenStore.lrange(f"FID:{fID}", 0, -1):
                self._TokenStore.delete(f"FID:{fID}")
            else:
                print("No Family Found")
        except Exception as e:
            raise InternalServerError("Failed to perform operation on token store")
        
    def update_keydata(self, kid: str, newKeyData: KeyMetadata, active:bool = True) -> None:
        '''Update key mapping on key rotation'''
        if active:
            self.latestKeyID = kid
            self.latestKeyMetadata = newKeyData

        self.kvsMapping[kid] = newKeyData
        if len(self.kvsMapping) > self.max_valid_keys:
            tokenManager.kvsMapping = dict(list(self.kvsMapping.items())[-self.max_valid_keys:]) 

    def check_key(self, kid: str) -> bool:
        '''Check database for a new key. If found, update keydata and return True.'''

        # Try to fetch a valid key with this KID 
        newKey: KeyData = self.db.session.execute(select(KeyData)
                                                 .where((KeyData.kid == kid) & (KeyData.expired_at.issnot(None)))
                                                 ).scalar_one_or_none()
        if not newKey:
            # Announce non existence to other workers in case they also receive this invalid key
            self._SyncedStore.set(f'invalid_key:{kid}', 1, self.announcement_duration)
            return False
        
        # Given key is indeed a valid key
        self.update_keydata(kid, KeyMetadata(newKey.public_pem, newKey.private_pem, 'ES256', newKey.epoch), active=not bool(newKey.rotated_out_at))   # An active key's rotated_out_at column will be None (__bool__ == False)
        return True
    
    def poll_store(self) -> None:
        '''Check synced store for an announcement for a new key. Intended to be run as a non-blocking, background task upon instantiation'''
        while True:
            try:
                key: dict = self._SyncedStore.get('NEW_KEY_ANNOUNCEMENT')
                if key and key != self.latestKeyID:
                    updated: bool = tokenManager.check_key(key)
                    if updated:
                        print(f"[BACKGROUND POLLER] Updated to new key: {key}")
                    else:
                        print(f"[BACKGROUND POLLER] Announced key {key} not valid in DB.")
            except Exception:
                print(f'[BACKGROUND POLLER]: Exception encountered. Traceback:')
                print(format_exc())
            finally:
                time.sleep(10)

    @staticmethod
    def generate_unique_identifier():
        return uuid.uuid4().hex
    
tokenManager: TokenManager = None
def init_token_manager(kvsMapping: dict[int, KeyMetadata], redisinterface: Redis, syncedstore: Redis, database: SQLAlchemy, **kwargs) -> None:
    global tokenManager
    tokenManager = TokenManager(kvsMapping=kvsMapping, interface=redisinterface, synced_store=syncedstore, db=database, **kwargs)