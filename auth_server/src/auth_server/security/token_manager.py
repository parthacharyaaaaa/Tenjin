import asyncio
import time
import uuid
from dataclasses import dataclass, field
from traceback import format_exc
from typing import Any, Literal, Optional, overload

import jwt
import jwt.exceptions as jwt_exceptions
from redis.asyncio import Redis

from auth_server.config.sub_config import KeyConfigModel, TokenManagerConfigModel
from auth_server.dependencies import get_app_config, get_keydata_repository
from auth_server.repositories.keydata import (
    KeydataRepository,
    KeyPrivateDataResult,
    KeyPublicDataResult,
)
from auth_server.security.tokens import (
    StandardAccessTokenClaims,
    StandardRefreshTokenClaims,
    TokenType,
)
from auth_server.strings import SyncedStoreStrings

# Type aliases
type TokenPair = tuple[str, str]


@dataclass(slots=True)
class TokenManager:
    _token_store_client: Redis
    _synced_store_client: Redis
    _keydata_repository: KeydataRepository = field(
        default_factory=get_keydata_repository
    )
    key_config: KeyConfigModel = field(default_factory=lambda: get_app_config().KEYS)
    token_manager_config: TokenManagerConfigModel = field(
        default_factory=lambda: get_app_config().JWKS.TOKEN_MANAGER
    )
    universal_claims: dict[str, Any] = field(default_factory=dict)
    universal_headers: dict[str, Any] = field(default_factory=dict)
    _polling_task: asyncio.Task[None] = field(init=False)
    _key_mapping: dict[str, KeyPublicDataResult] = field(
        default_factory=dict, init=False
    )
    _active_key: str = field(init=False, default="__UNINITIALIZED__")
    _active_key_private_pem: bytes = field(init=False, default=b"__UNINITIALIZED__")

    def __post_init__(self):
        # Initialize universal headers, common to all tokens issued in any context
        self.universal_headers.update(
            {"typ": "JWT", "alg": str(self.key_config.SIGNATURE_ALGORITHM)}
        )
        # Start background task for polling
        self._polling_task = asyncio.create_task(
            self.poll_store(self.token_manager_config.POLL_INTERVAL),
            name="polling_task",
        )

    def set_key_state(
        self,
        active_key_data: KeyPrivateDataResult,
        verification_keys_mapping: dict[str, KeyPublicDataResult] | None = None,
    ) -> None:
        verification_keys_mapping = verification_keys_mapping or {}
        self._key_mapping: dict[str, KeyPublicDataResult] = (
            verification_keys_mapping
            | {active_key_data.kid: active_key_data.create_public_copy()}
        )
        self._active_key = active_key_data.kid
        self._active_key_private_pem = active_key_data.private_pem

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
            if kid not in self._key_mapping:
                raise jwt_exceptions.InvalidKeyError(
                    "This key is not recognised, meaning it is possibly tampered, forged, or simply expired a long time ago."
                )

            decoded_token: dict[str, Any] = jwt.decode(
                jwt=token,
                key=self._key_mapping[kid].public_pem,
                algorithms=[self._key_mapping[kid].alg],
                leeway=self.token_manager_config.LEEWAY,
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
            "exp": time.time() + self.token_manager_config.REFRESH_LIFETIME,
            "nbf": time.time()
            + self.token_manager_config.ACCESS_LIFETIME
            - self.token_manager_config.LEEWAY,
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
            key=self._active_key_private_pem,
            algorithm=self._key_mapping[self._active_key].alg,
            headers=self.universal_headers | {"kid": self._active_key},
        )

    def issue_access_token(
        self, sub: str, sid: int, family_id: str, additional_claims: dict | None = None
    ) -> str:
        payload: dict = {
            "iat": time.time(),
            "exp": time.time() + self.token_manager_config.ACCESS_LIFETIME,
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
            key=self._active_key_private_pem,
            algorithm=self._key_mapping[self._active_key].alg,
            headers=self.universal_headers | {"kid": self._active_key},
        )

    async def shift_token_window(self, family_id: str) -> None:
        """Revokes the oldest refresh token from a family if capacity is reached, without invalidating the entire family"""
        try:
            llen: int = await self._token_store_client.llen(f"FID:{family_id}")  # type: ignore[reportAssignmentType]

            if llen == 0:
                return

            if llen >= self.token_manager_config.MAX_TOKENS_PER_FAMILY:
                await self._token_store_client.rpop(  # pyrefly: ignore[not-async]
                    f"FID:{family_id}",
                    max(1, llen - self.token_manager_config.MAX_TOKENS_PER_FAMILY),
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
        self, kid: str, new_keydata: KeyPrivateDataResult, active: bool = True
    ) -> None:
        """Update key mapping on key rotation"""
        if active:
            self._active_key = kid
            self._active_key_private_pem = new_keydata.private_pem

        self._key_mapping[kid] = new_keydata.create_public_copy()

    async def fetch_unexpired_key(self, kid: str) -> KeyPrivateDataResult | None:
        key_label: str = f"invalid_key:{kid}"
        # Pre-emptive negative check
        invalid_key: str | None = await self._synced_store_client.get(key_label)
        if invalid_key:
            return None

        key: KeyPrivateDataResult | None = await self._keydata_repository.get_keydata(
            kid, public_only=False
        )
        if not key:
            self._synced_store_client.set(
                key_label, 1, self.token_manager_config.ANNOUNCEMENT_DURATION
            )
            return None
        return key

    def invalidate_key(self, kid: str) -> None:
        """Invalidate a verification key"""
        if self._active_key == kid:
            raise RuntimeError("Cannot invalidate active signing key")

        self._key_mapping.pop(kid, None)

    async def poll_store(self, interval: int) -> None:
        """
        Check synced store to keep local keys updated with global keys.
        Intended to be run as a non-blocking, background task upon instantiation
        """
        while True:
            try:
                valid_keys: list[str] | None = await self._synced_store_client.lrange(  # pyrefly: ignore[not-async]
                    SyncedStoreStrings.VALID_KEYS, 0, -1
                )

                if not valid_keys:
                    raise RuntimeError("Valid keys list empty or not found")

                global_valid_keyset: frozenset[str] = frozenset(
                    key for key in valid_keys
                )
                local_valid_keyset: frozenset[str] = frozenset(self._key_mapping.keys())

                new_valid_keys: frozenset[str] = (
                    global_valid_keyset - local_valid_keyset
                )
                for new_key in new_valid_keys:
                    print(
                        f"[BACKGROUND POLLER]: Adding verification new key {new_key}..."
                    )
                    result: (
                        KeyPrivateDataResult | None
                    ) = await self.fetch_unexpired_key(new_key)
                    if result:
                        self.update_keydata(
                            new_key, result, active=not bool(result.rotated_out_at)
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
