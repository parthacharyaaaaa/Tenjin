import base64
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Literal, overload

import orjson
from auxillary.singleton import SingletonMetaclass
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from redis.asyncio.client import Redis
from sqlalchemy.util.typing import Final

from auth_server.config import AdminConfigModel
from auth_server.models.session import AdminSession
from auth_server.security.admin_roles import AdminRole
from auth_server.strings import GENERIC_SESSION_SEPARATOR, generate_admin_session_name


@dataclass(frozen=True, slots=True)
class AdminSessionManager(metaclass=SingletonMetaclass):
    session_store: Redis
    admin_config: AdminConfigModel
    session_delimiter_symbol: str = field(
        default=GENERIC_SESSION_SEPARATOR, kw_only=True
    )
    string_encoding: str = field(default="ascii", init=False)

    def b64encode(self, data: bytes | bytearray) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode(self.string_encoding)

    @overload
    async def get_admin_session(
        self, admin_id: int, *, missing_ok: Literal[False]
    ) -> AdminSession: ...

    @overload
    async def get_admin_session(
        self, admin_id: int, *, missing_ok: Literal[True] = True
    ) -> AdminSession | None: ...

    async def get_admin_session(
        self, admin_id: int, *, missing_ok: bool = True
    ) -> AdminSession | None:
        admin_session_dict: dict[str, Any] = await self.session_store.hgetall(  # pyrefly: ignore[not-async]
            generate_admin_session_name(admin_id)
        )

        if not admin_session_dict:
            if missing_ok:
                return None
            raise ValueError(f"No session found for admin with ID {admin_id}")
        return AdminSession.model_validate(**admin_session_dict)

    def create_signed_session_token(
        self, session_token: bytes | bytearray, signing_pem: bytes | bytearray
    ) -> str:
        signing_key: PrivateKeyTypes = load_pem_private_key(signing_pem, password=None)
        if not isinstance(signing_key, ec.EllipticCurvePrivateKey):
            raise ValueError(
                "Expected signing key of type: ",
                str(ec.EllipticCurvePrivateKey),
                f"Got: {type(signing_key)}",
            )

        signature: Final[bytes] = signing_key.sign(
            session_token, self.admin_config.PREHASHED_SESSION_SIGNATURE_ALGORITHM
        )

        return self.session_delimiter_symbol.join(
            map(self.b64encode, (session_token, signature))
        )

    def generate_session_token(
        self,
        session: AdminSession,
    ) -> bytes:
        mapping: dict[str, Any] = session.model_dump()
        del mapping["revival_digest"]
        return base64.urlsafe_b64encode(orjson.dumps(mapping))

    def derive_session_expiry(self, session_epoch: int) -> int:
        return session_epoch + self.admin_config.ADMIN_SESSION_DURATION

    def generate_session_revival_digest(
        self,
    ) -> str:
        return secrets.token_hex(self.admin_config.REVIVAL_DIGEST_LENGTH)

    async def initialize_session(
        self, admin_id: int, role: AdminRole, signing_key_pem: bytes | bytearray
    ) -> tuple[str, str]:
        session_epoch: Final[int] = int(time.monotonic())
        admin_session: Final[AdminSession] = AdminSession(
            admin_id=admin_id,
            epoch_timestamp=session_epoch,
            expiry_timestamp=self.derive_session_expiry(session_epoch),
            revival_digest=self.generate_session_revival_digest(),
            role=role,
        )
        signed_session_token = self.create_signed_session_token(
            self.generate_session_token(admin_session), signing_key_pem
        )

        await self.session_store.hset(  # pyrefly: ignore[not-async]
            generate_admin_session_name(admin_id),
            mapping=admin_session.model_dump_redis(),
        )

        return signed_session_token, admin_session.revival_digest

    async def refresh_session(
        self, session: AdminSession, signing_key_pem: bytes | bytearray
    ) -> tuple[str, str]:
        session_epoch: Final[int] = int(time.monotonic())
        admin_session: Final[AdminSession] = AdminSession.construct_session_successor(
            session,
            session_epoch,
            self.derive_session_expiry(session_epoch),
            self.generate_session_revival_digest(),
        )
        signed_session_token = self.create_signed_session_token(
            self.generate_session_token(admin_session), signing_key_pem
        )

        await self.session_store.hset(  # pyrefly: ignore[not-async]
            generate_admin_session_name(admin_session.admin_id),
            mapping=admin_session.model_dump_redis(),
        )

        return signed_session_token, admin_session.revival_digest
