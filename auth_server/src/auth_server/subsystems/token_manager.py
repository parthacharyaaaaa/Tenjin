import logging
import time
import uuid
from collections.abc import AsyncGenerator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from traceback import format_exc
from types import MappingProxyType
from typing import Any, Final, Literal, overload

import jwt
import jwt.exceptions as jwt_exceptions
from redis.asyncio import Redis

from auth_server.config.sub_config import KeyConfigModel, TokenManagerConfigModel
from auth_server.datastructures.exceptions.token import (
    ImmatureRefreshAttemptError,
    InvalidKeyError,
    InvalidTokenError,
    MissingTokenFamilyError,
    TamperedTokenError,
    TokenFamilyExistsError,
    TokenMismatchError,
)
from auth_server.repositories.keydata import (
    EllipticCurveJWKResult,
    KeydataRepository,
    KeyPrivateDataResult,
    KeyPublicDataResult,
)
from auth_server.tokens.typing import (
    StandardAccessTokenClaims,
    StandardRefreshTokenClaims,
    TokenType,
)

# Type aliases
type TokenPair = tuple[str, str]


@dataclass(slots=True)
class TokenManager:
    _token_store_client: Redis
    _synced_store_client: Redis
    _keydata_repository: KeydataRepository
    key_config: KeyConfigModel
    token_manager_config: TokenManagerConfigModel
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__file__))
    universal_claims: dict[str, Any] = field(default_factory=dict)
    universal_headers: dict[str, Any] = field(default_factory=dict)
    _key_mapping: dict[str, KeyPublicDataResult] = field(
        default_factory=dict, init=False
    )
    _active_key: str = field(init=False, default="__UNINITIALIZED__")
    _active_key_private_pem: bytes = field(init=False, default=b"__UNINITIALIZED__")

    def __post_init__(self):
        # Initialize universal headers, common to all tokens issued in any context
        self.universal_headers.update(
            {"typ": "JWT", "alg": self.key_config.SIGNATURE_ALGORITHM}
        )

    @staticmethod
    def generate_family_list_id(identifier: str) -> str:
        return f"FID:{identifier}"

    @staticmethod
    def generate_family_record(jti: str, exp: int | float) -> str:
        return f"{jti}:{exp}"

    @asynccontextmanager
    async def transactional_block(self) -> AsyncGenerator[None, None]:
        keys_snapshot: Final[MappingProxyType] = MappingProxyType(
            self._key_mapping.copy()
        )
        active_key_id_snapshot: Final[str] = self._active_key
        active_key_pem_snapshot: Final[bytes] = self._active_key_private_pem
        try:
            yield
        except Exception:
            self._key_mapping = dict(keys_snapshot)
            self._active_key = active_key_id_snapshot
            self._active_key_private_pem = active_key_pem_snapshot
            raise

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
        self,
        token: str,
        token_type: Literal[TokenType.StandardAccess] = TokenType.StandardAccess,
        **kwargs,
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
                raise InvalidKeyError(
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
        except jwt_exceptions.InvalidSignatureError:
            if token_type == TokenType.StandardRefresh:
                await self.invalidate_family(
                    jwt.decode(token, options={"verify_signature": False})["fid"]
                )
            raise TamperedTokenError("Token is tampered, please login again")
        except jwt_exceptions.ImmatureSignatureError:
            raise ImmatureRefreshAttemptError("Premature token reissuance")
        except jwt_exceptions.InvalidTokenError:
            raise InvalidTokenError("Invalid token, please login again")
        except KeyError as e:
            raise InvalidTokenError("Token headers missing key ID") from e

    async def reissue_token_pair(self, refresh_token: str) -> TokenPair:
        decoded_token: StandardRefreshTokenClaims = await self.decode_token(
            refresh_token, token_type=TokenType.StandardRefresh
        )
        refresh_token = await self.issue_refresh_token(
            decoded_token["sub"],
            decoded_token["sid"],
            existing_family_validation_data=(
                decoded_token["jti"],
                decoded_token["exp"],
            ),
        )
        access_token: str = self.issue_access_token(
            decoded_token["sub"],
            decoded_token["sid"],
            family_id=decoded_token["fid"],
        )

        return refresh_token, access_token

    async def issue_refresh_token(
        self,
        sub: str,
        sid: int,
        *,
        existing_family_validation_data: tuple[str, int] | None = None,
        additional_claims: dict | None = None,
    ) -> str:
        family_id: Final[str] = self.generate_family_list_id(sub)
        if existing_family_validation_data:
            # Check for replay attack
            key: str | None = await self._token_store_client.lindex(  # pyrefly: ignore[not-async]
                family_id, 0
            )
            if not key:
                await self.invalidate_family(family_id)
                raise MissingTokenFamilyError(
                    f"Token family {family_id} is invalid or empty"
                )

            key_metadata = key.split(":")
            if (
                key_metadata[0] != existing_family_validation_data[0]
                or int(key_metadata[1]) != existing_family_validation_data[1]
            ):
                await self.invalidate_family(family_id)
                raise TokenMismatchError(
                    f"Replay attack detected or token metadata mismatch for family {family_id}"
                )
        else:
            if await self._token_store_client.llen(family_id) != 0:  # pyrefly: ignore[not-async]
                raise TokenFamilyExistsError(
                    f"Family ID {family_id} exists in token store when implied not to"
                )

        # All checks passed
        token_epoch_reference: Final[int] = int(time.time())
        payload: dict = {
            "iat": token_epoch_reference,
            "exp": token_epoch_reference
            + int(self.token_manager_config.REFRESH_LIFETIME.total_seconds()),
            "nbf": token_epoch_reference
            + int(self.token_manager_config.ACCESS_LIFETIME.total_seconds())
            - int(self.token_manager_config.LEEWAY.total_seconds()),
            "fid": family_id,
            "sub": sub,
            "sid": sid,
            "jti": self.generate_unique_identifier(),
        }

        payload.update(self.universal_claims)
        if additional_claims:
            payload.update(additional_claims)

        previous_family_length: Final[int] = await self._token_store_client.llen(
            family_id
        )  # pyrefly: ignore[not-async]
        async with self._token_store_client.pipeline(transaction=True) as pipe:
            pipe.lpush(
                family_id, self.generate_family_record(payload["jti"], payload["exp"])
            )
            pipe.expireat(family_id, payload["exp"])
            if (
                previous_family_length
                >= self.token_manager_config.MAX_TOKENS_PER_FAMILY
            ):
                pipe.rpop(
                    family_id,
                    max(
                        1,
                        previous_family_length
                        - self.token_manager_config.MAX_TOKENS_PER_FAMILY,
                    ),
                )
            await pipe.execute()

        return jwt.encode(
            payload=payload,
            key=self._active_key_private_pem,
            algorithm=self._key_mapping[self._active_key].alg,
            headers=self.universal_headers | {"kid": self._active_key},
        )

    def issue_access_token(
        self,
        sub: str,
        sid: int,
        *,
        additional_claims: dict | None = None,
        family_id: str | None = None,
    ) -> str:
        token_epoch_reference: Final[int] = int(time.time())
        payload: dict = {
            "iat": token_epoch_reference,
            "exp": token_epoch_reference
            + int(self.token_manager_config.ACCESS_LIFETIME.total_seconds()),
            "fid": self.generate_family_list_id(sub)
            if family_id is None
            else family_id,
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

    async def invalidate_family(self, family_id: str) -> None:
        """Remove entire token family from revocation list and token store"""
        try:
            if await self._token_store_client.lrange(family_id, 0, -1):  # type: ignore[reportGeneralTypeIssues]
                await self._token_store_client.delete(family_id)
            else:
                self.logger.info("No Family Found")
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

    def invalidate_key(self, kid: str) -> None:
        """Invalidate a verification key"""
        if self._active_key == kid:
            raise RuntimeError("Cannot invalidate active signing key")

        self._key_mapping.pop(kid, None)

    async def update_local_store(
        self, global_jwks: Iterable[EllipticCurveJWKResult]
    ) -> None:
        """
        Check synced store to keep local keys updated with global keys.
        Intended to be run as a non-blocking, background task upon instantiation
        """
        try:
            global_valid_keyset: frozenset[str] = frozenset(k.kid for k in global_jwks)
            local_valid_keyset: frozenset[str] = frozenset(self._key_mapping.keys())

            for new_key in global_valid_keyset - local_valid_keyset:
                self.logger.info(f"Adding verification new key {new_key}...")
                result: (
                    KeyPrivateDataResult | None
                ) = await self._keydata_repository.get_keydata(
                    new_key, public_only=False
                )
                if result:
                    self.update_keydata(
                        new_key, result, active=result.rotated_out_at is None
                    )
                    self.logger.info(
                        f"Added verification new key {new_key} to local token manager"
                    )

            for expired_key in local_valid_keyset - global_valid_keyset:
                self.logger.info(f"Invalidating local key {expired_key}...")
                self.invalidate_key(expired_key)
                self.logger.info(f"Invalidated local key {expired_key}")

        except Exception:
            self.logger.error("Exception encountered. Traceback:")
            self.logger.error(format_exc())

    @staticmethod
    def generate_unique_identifier():
        return uuid.uuid4().hex
