import asyncio
import time
import uuid
from traceback import format_exc
from typing import Any, Final, Literal, Optional, TypeAlias, overload

import jwt
import jwt.exceptions as jwt_exceptions
from redis.asyncio import Redis

from auth_server.models.database import KeyData
from auth_server.repositories.keydata import KeydataRepository
from auth_server.security.key_container import KeyMetadata
from auth_server.security.tokens import (
    StandardAccessTokenClaims,
    StandardRefreshTokenClaims,
    TokenType,
)
from auth_server.strings import SyncedStoreStrings

# Type aliases
TokenPair: TypeAlias = tuple[str, str]


class TokenManager:
    active_refresh_tokens: int = 0

    def __init__(
        self,
        interface: Redis,
        synced_store: Redis,
        keydata_repository: KeydataRepository,
        refresh_lifetime: int = 60 * 60 * 3,
        access_lifetime: int = 60 * 30,
        alg: str = "ES256",
        universal_claims: dict | None = None,
        universal_headers: dict | None = None,
        leeway: int = 180,
        max_tokens_per_fid: int = 3,
        max_valid_keys: int = 3,
        announcement_duration: int = 60 * 60 * 3,
        poll_interval: int = 30,
    ):
        try:
            self._token_store_client = interface
            self.max_llen = max_tokens_per_fid
        except Exception as e:
            raise ValueError(
                "Mandatory configurations missing for _token_store_client"
            ) from e

        self.announcement_duration = announcement_duration

        self.synced_store_client = synced_store

        self.keydata_repository = keydata_repository

        # Initialize universal headers, common to all tokens issued in any context
        universal_headers = {"typ": "JWT", "alg": alg}
        if universal_headers:
            universal_headers.update(universal_headers)
        self.universal_headers = universal_headers
        # Initialize universal claims, common to all tokens issued in any context.
        # These should at the very least contain registered claims like "exp"
        self.universal_claims = universal_claims or {}

        self.refresh_lifetime = refresh_lifetime
        self.access_lifetime = access_lifetime

        # Set leeway for time-related claims
        self.leeway = leeway
        self.max_valid_keys = max_valid_keys

        # Start background thread for polling
        self.polling_task: Final[asyncio.Task] = asyncio.create_task(
            self.poll_store(poll_interval), name="polling_task"
        )

    def set_key_state(
        self,
        active_kid: str,
        active_key_metadata: KeyMetadata,
        verification_keys_mapping: dict[str, KeyMetadata] | None = None,
    ) -> None:
        if not verification_keys_mapping:
            verification_keys_mapping = {}
        self.key_mapping: dict[str, KeyMetadata] = verification_keys_mapping | {
            active_kid: active_key_metadata
        }
        self.active_key = active_kid

    @overload
    async def decode_token(
        self, token: str, token_type: Literal[TokenType.StandardAccess], **kwargs
    ) -> StandardAccessTokenClaims: ...

    @overload
    async def decode_token(
        self, token: str, token_type: Literal[TokenType.StandardRefresh], **kwargs
    ) -> StandardRefreshTokenClaims: ...

    async def decode_token(
        self, token: str, token_type: TokenType = TokenType.StandardAccess, **kwargs
    ) -> StandardAccessTokenClaims | StandardRefreshTokenClaims:
        try:
            kid: int = jwt.get_unverified_header(token)["kid"]
            if kid not in self.key_mapping:
                raise jwt_exceptions.InvalidKeyError(
                    "This key is not recognised, meaning it is possibly tampered, forged, or simply expired a long time ago."
                )

            decoded_token: dict[str, Any] = jwt.decode(
                jwt=token,
                key=self.key_mapping[kid].PUBLIC_PEM,
                algorithms=[self.key_mapping[kid].ALGORITHM],
                leeway=self.leeway,
                options=kwargs.get("options"),
            )
            if token_type == TokenType.StandardAccess:
                return StandardAccessTokenClaims(**decoded_token)
            return StandardRefreshTokenClaims(**decoded_token)
        except (
            jwt_exceptions.ImmatureSignatureError,
            jwt_exceptions.InvalidIssuedAtError,
            jwt_exceptions.InvalidIssuerError,
        ) as e:
            if token_type == TokenType.StandardRefresh:
                await self.invalidate_family(
                    jwt.decode(token, options={"verify_signature": False})["fid"]
                )
            raise ValueError("Invalid Token") from e
        except KeyError as e:
            raise jwt_exceptions.InvalidTokenError(
                "Token headers missing key ID"
            ) from e

    async def reissue_token_pair(self, refresh_token: str) -> TokenPair:
        decoded_token: StandardRefreshTokenClaims = await self.decode_token(
            refresh_token, token_type=TokenType.StandardRefresh
        )

        refresh_token = await self.issue_refresh_token(
            decoded_token["sub"],
            decoded_token["sid"],
            jti=decoded_token["jti"],
            family_id=decoded_token["fid"],
            exp=decoded_token["exp"],
        )

        await self.shift_token_window(decoded_token["fid"])

        access_token: str = self.issue_access_token(
            decoded_token["sub"],
            decoded_token["sid"],
            decoded_token["fid"],
        )

        return refresh_token, access_token

    async def issue_refresh_token(
        self,
        sub: str,
        sid: int,
        family_id: str,
        additional_claims: Optional[dict] = None,
        jti: Optional[str] = None,
        exp: Optional[int | float] = None,
    ) -> str:
        if family_id:
            # Check for replay attack
            key: str | None = await self._token_store_client.lindex(  # pyrefly: ignore[not-async]
                f"FID:{family_id}", 0
            )
            if not key:
                await self.invalidate_family(family_id)
                raise ValueError(f"Token family {family_id} is invalid or empty")

            key_metadata = key.split(":")
            if key_metadata[0] != jti or float(key_metadata[1]) != exp:
                await self.invalidate_family(family_id)
                raise ValueError(
                    f"Replay attack detected or token metadata mismatch for family {family_id}"
                )

        # Fresh token being issued
        elif await self._token_store_client.lrange(f"FID:{family_id}", 0, -1):  # type: ignore[reportGeneralTypeIssues]
            await self.invalidate_family(family_id)

        # All checks passed
        payload: dict = {
            "iat": time.time(),
            "exp": time.time() + self.refresh_lifetime,
            "nbf": time.time() + self.access_lifetime - self.leeway,
            "fid": family_id,
            "sub": sub,
            "sid": sid,
            "jti": self.generate_unique_identifier(),
        }
        payload.update(self.universal_claims)
        if additional_claims:
            payload.update(additional_claims)

        async with self._token_store_client.pipeline(transaction=False) as pipe:
            pipe.lpush(f"FID:{family_id}", f"{payload['jti']}:{payload['exp']}")
            pipe.expireat(f"FID:{family_id}", int(payload["exp"]))
            await pipe.execute()

        return jwt.encode(
            payload=payload,
            key=self.key_mapping[self.active_key].PRIVATE_PEM,
            algorithm=self.key_mapping[self.active_key].ALGORITHM,
            headers=self.universal_headers | {"kid": self.active_key},
        )

    def issue_access_token(
        self, sub: str, sid: int, family_id: str, additional_claims: dict | None = None
    ) -> str:
        payload: dict = {
            "iat": time.time(),
            "exp": time.time() + self.access_lifetime,
            "fid": family_id,
            "sub": sub,
            "sid": sid,
            "jti": self.generate_unique_identifier(),
        }
        payload.update(self.universal_claims)
        if additional_claims:
            payload.update(additional_claims)

        return jwt.encode(
            payload=payload,
            key=self.key_mapping[self.active_key].PRIVATE_PEM,
            algorithm=self.key_mapping[self.active_key].ALGORITHM,
            headers=self.universal_headers | {"kid": self.active_key},
        )

    async def shift_token_window(self, family_id: str) -> None:
        """Revokes the oldest refresh token from a family if capacity is reached, without invalidating the entire family"""
        try:
            llen: int = await self._token_store_client.llen(f"FID:{family_id}")  # type: ignore[reportAssignmentType]

            if llen == 0:
                return

            if llen >= self.max_llen:
                await self._token_store_client.rpop(  # pyrefly: ignore[not-async]
                    f"FID:{family_id}", max(1, llen - self.max_llen)
                )
        except Exception as e:
            raise RuntimeError("Failed to perform operation on token store") from e

    async def invalidate_family(self, family_id: str) -> None:
        """Remove entire token family from revocation list and token store"""
        try:
            if await self._token_store_client.lrange(f"FID:{family_id}", 0, -1):  # type: ignore[reportGeneralTypeIssues]
                await self._token_store_client.delete(f"FID:{family_id}")
            else:
                print("No Family Found")
        except Exception as e:
            raise RuntimeError("Failed to perform operation on token store") from e

    def update_keydata(
        self, kid: str, new_keydata: KeyMetadata, active: bool = True
    ) -> None:
        """Update key mapping on key rotation"""
        if active:
            self.active_key = kid

        self.key_mapping[kid] = new_keydata

    async def fetch_unexpired_key(self, kid: str) -> KeyMetadata | None:
        """Fetch a non-expired key from the database
        Args:
            kid: Key ID to query the database for

        Returns:
            Fetched key casted to KeyMetadata, None if not found"""
        # Check synced store for an invalid key announcement for this key
        invalid_key: str | None = await self.synced_store_client.get(
            f"invalid_key:{kid}"
        )
        if invalid_key:
            return None

        # Try to fetch a valid key with this KID
        key: KeyData | None = await self.keydata_repository.get_keydata(kid)
        if not key:
            # Announce non existence to other workers in case they also receive this invalid key
            self.synced_store_client.set(
                f"invalid_key:{kid}", 1, self.announcement_duration
            )
            return None
        return KeyMetadata(
            key.public_pem,
            key.private_pem,
            key.alg,
            key.epoch.timestamp(),
            key.rotated_out_at.timestamp(),
        )

    def invalidate_key(self, kid: str) -> None:
        """Invalidate a verification key"""
        if self.active_key == kid:
            raise RuntimeError("Cannot invalidate active signing key")

        self.key_mapping.pop(kid, None)

    async def poll_store(self, interval: int) -> None:
        """Check synced store to keep local keys updated with global keys. Intended to be run as a non-blocking, background task upon instantiation"""
        while True:
            try:
                valid_keys: list[str] | None = await self.synced_store_client.lrange(  # pyrefly: ignore[not-async]
                    SyncedStoreStrings.VALID_KEYS, 0, -1
                )

                if not valid_keys:
                    raise RuntimeError("Valid keys list empty or not found")

                global_valid_keyset: frozenset[str] = frozenset(
                    key for key in valid_keys
                )
                local_valid_keyset: frozenset[str] = frozenset(self.key_mapping.keys())

                new_valid_keys: frozenset[str] = (
                    global_valid_keyset - local_valid_keyset
                )
                for new_key in new_valid_keys:
                    print(
                        f"[BACKGROUND POLLER]: Adding verification new key {new_key}..."
                    )
                    result: KeyMetadata | None = await self.fetch_unexpired_key(new_key)
                    if result:
                        self.update_keydata(
                            new_key, result, active=not bool(result.ROTATED_AT)
                        )  # If rotated out, them update key mapping with a verification key, else with an active key
                        print(
                            f"[BACKGROUND POLLER]: Added verification new key {new_key} to local token manager"
                        )

                # Eliminate expired keys from memory. This is done after adding any new keys to local mapping
                expired_local_keys: frozenset[str] = (
                    local_valid_keyset - global_valid_keyset
                )
                for expired_key in expired_local_keys:
                    print(
                        f"[BACKGROUND POLLER]: Invalidating local key {expired_key}..."
                    )
                    self.invalidate_key(expired_key)
                    print(f"[BACKGROUND POLLER]: Invalidated local key {expired_key}")

            except Exception:
                print("[BACKGROUND POLLER]: Exception encountered. Traceback:")
                print(format_exc())
            finally:
                await asyncio.sleep(interval)

    @staticmethod
    def generate_unique_identifier():
        return uuid.uuid4().hex
