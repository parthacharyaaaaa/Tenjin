import base64
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Literal, overload

import pydantic
from auxillary.singleton import SingletonMetaclass
from redis.asyncio.client import Redis
from sqlalchemy.util.typing import Final

from auth_server.admin.roles import AdminRole
from auth_server.config import AdminConfigModel
from auth_server.models.session import AdminSession
from auth_server.strings import GENERIC_SESSION_SEPARATOR


@dataclass(frozen=True, slots=True)
class AdminSessionManager(metaclass=SingletonMetaclass):
    session_store: Redis
    admin_config: AdminConfigModel
    session_delimiter_symbol: str = field(
        default=GENERIC_SESSION_SEPARATOR, kw_only=True
    )
    admin_session_name_prefix: str = field(default="admin", kw_only=True)
    admin_session_reverse_mapping_name: str = field(
        default="active_admins", kw_only=True
    )
    string_encoding: str = field(default="ascii", init=False)
    session_identifier_byte_length: int = field(default=16)

    def b64encode(self, data: bytes | bytearray) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode(self.string_encoding)

    @overload
    async def get_admin_session(
        self, session_id: str, *, missing_ok: Literal[False]
    ) -> AdminSession: ...

    @overload
    async def get_admin_session(
        self, session_id: str, *, missing_ok: Literal[True] = True
    ) -> AdminSession | None: ...

    async def get_admin_session(
        self, session_id: str, *, missing_ok: bool = True
    ) -> AdminSession | None:
        session_name: Final[str] = self.generate_admin_session_name(session_id)
        admin_session_dict: dict[str, Any] = await self.session_store.hgetall(  # pyrefly: ignore[not-async]
            session_name
        )

        if not admin_session_dict:
            if missing_ok:
                return None
            raise ValueError(f"No session found with ID: {session_id}")

        try:
            return AdminSession.model_validate(**admin_session_dict)
        except pydantic.ValidationError as e:
            await self.terminate_malformed_session(
                session_name, admin_id=admin_session_dict.get("admin_id")
            )
            raise ValueError("Invalid session, please login again") from e

    @overload
    async def get_admin_session_via_admin_id(
        self, admin_id: int, *, missing_ok: Literal[False]
    ) -> AdminSession: ...

    @overload
    async def get_admin_session_via_admin_id(
        self, admin_id: int, *, missing_ok: Literal[True] = True
    ) -> AdminSession | None: ...

    async def get_admin_session_via_admin_id(
        self, admin_id: int, *, missing_ok: bool = True
    ) -> AdminSession | None:
        session_id: str | None = await self.session_store.hget(  # pyrefly: ignore[not-async]
            self.admin_session_reverse_mapping_name, str(admin_id)
        )
        if not session_id:
            if missing_ok:
                return None
            raise ValueError(f"No session found for admin with ID: {admin_id}")
        return await self.get_admin_session(session_id, missing_ok=missing_ok)

    def derive_session_expiry(self, session_epoch: int) -> int:
        return session_epoch + int(
            self.admin_config.ADMIN_SESSION_DURATION.total_seconds()
        )

    def generate_session_revival_digest(self) -> str:
        return secrets.token_hex(self.admin_config.REVIVAL_DIGEST_LENGTH)

    def generate_session_id(self) -> str:
        return secrets.token_urlsafe(self.session_identifier_byte_length)

    async def _register_admin_session(
        self, admin_session: AdminSession, *, preceding_session_id: str | None = None
    ) -> tuple[str, str]:
        async with self.session_store.pipeline(transaction=True) as pipeline:
            pipeline.hset(
                self.generate_admin_session_name(admin_session.session_id),
                mapping=admin_session.model_dump_redis(),
            )
            pipeline.hsetex(
                self.admin_session_reverse_mapping_name,
                str(admin_session.admin_id),
                ex=self.admin_config.ADMIN_SESSION_DURATION
                * self.admin_config.MAX_SESSION_ITERATIONS,
            )
            if preceding_session_id is not None:
                pipeline.hdel(
                    self.admin_session_reverse_mapping_name, str(admin_session.admin_id)
                )
                pipeline.delete(preceding_session_id)
            await pipeline.execute()

        return admin_session.session_id, admin_session.revival_digest

    async def initialize_session(
        self, admin_id: int, role: AdminRole
    ) -> tuple[str, str]:
        session_epoch: Final[int] = int(time.monotonic())
        admin_session: Final[AdminSession] = AdminSession(
            session_id=self.generate_session_id(),
            admin_id=admin_id,
            epoch_timestamp=session_epoch,
            expiry_timestamp=self.derive_session_expiry(session_epoch),
            revival_digest=self.generate_session_revival_digest(),
            role=role,
        )
        return await self._register_admin_session(admin_session)

    async def refresh_session(self, session: AdminSession) -> tuple[str, str]:
        session_epoch: Final[int] = int(time.monotonic())
        admin_session: Final[AdminSession] = AdminSession.construct_session_successor(
            session,
            self.generate_session_id(),
            session_epoch,
            self.derive_session_expiry(session_epoch),
            self.generate_session_revival_digest(),
        )
        return await self._register_admin_session(
            admin_session, preceding_session_id=session.session_id
        )

    async def terminate_session(self, session_id: str, admin_id: int) -> None:
        async with self.session_store.pipeline() as pipeline:
            pipeline.delete(self.generate_admin_session_name(session_id))
            pipeline.hdel(self.admin_session_reverse_mapping_name, str(admin_id))
            await pipeline.execute()

    async def terminate_malformed_session(
        self, session_id: str, *, admin_id: int | None = None
    ) -> None:
        async with self.session_store.pipeline() as pipeline:
            pipeline.delete(self.generate_admin_session_name(session_id))
            if admin_id:
                pipeline.hdel(self.admin_session_reverse_mapping_name, str(admin_id))
            await pipeline.execute()

    async def terminate_session_via_object(self, session: AdminSession) -> None:
        return await self.terminate_session(session.session_id, session.admin_id)

    def generate_admin_session_name(self, session_id: str) -> str:
        return self.session_delimiter_symbol.join(
            (
                self.admin_session_name_prefix,
                session_id,
            )
        )
