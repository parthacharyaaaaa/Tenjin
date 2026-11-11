import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, overload

import pydantic
from auxillary.data_structures.redis_script_registry import RedisScriptRegistry
from auxillary.singleton import SingletonMetaclass
from redis.asyncio.client import Redis
from sqlalchemy.util.typing import Final

from auth_server.admin.roles import AdminRole
from auth_server.config import AdminConfigModel
from auth_server.datastructures.exceptions.admin import (
    InvalidaSessionError,
    MissingSessionError,
)
from auth_server.datastructures.redis.script_names import RedisScriptName
from auth_server.models.session import AdminSession
from auth_server.strings import GENERIC_SESSION_SEPARATOR


@dataclass(frozen=True, slots=True, weakref_slot=True)
class AdminSessionManager(metaclass=SingletonMetaclass):
    session_store: Redis
    script_registry: RedisScriptRegistry
    admin_config: AdminConfigModel
    session_delimiter_symbol: str = field(
        default=GENERIC_SESSION_SEPARATOR, kw_only=True
    )
    admin_session_name_prefix: str = field(default="admin", kw_only=True)
    admin_session_reverse_mapping_name: str = field(
        default="active_admins", kw_only=True
    )
    admin_last_auth_activity_map_name: str = field(
        default="last_auth_activities", kw_only=True
    )
    session_identifier_byte_length: int = field(default=16)

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
        admin_session_dict: dict[str, Any] = await self.session_store.hgetall(  # pyrefly: ignore[not-async, bad-assignment]
            session_name
        )

        if not admin_session_dict:
            if missing_ok:
                return None
            raise MissingSessionError(f"No session found with ID: {session_id}")

        try:
            return AdminSession.model_validate(admin_session_dict)
        except pydantic.ValidationError as e:
            await self.terminate_malformed_session(
                session_name, admin_id=admin_session_dict.get("admin_id")
            )
            raise InvalidaSessionError("Invalid session, please login again") from e

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
        session_id: str | None = await self.session_store.hget(  # pyrefly: ignore[not-async, bad-assignment]
            self.admin_session_reverse_mapping_name, str(admin_id)
        )
        if not session_id:
            if missing_ok:
                return None
            raise MissingSessionError(f"No session found for admin with ID: {admin_id}")
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
            pipeline.hset(
                self.admin_session_reverse_mapping_name,
                str(admin_session.admin_id),
                admin_session.session_id,
            )
            pipeline.hexpire(
                self.admin_session_reverse_mapping_name,
                self.admin_config.ADMIN_SESSION_DURATION
                * self.admin_config.MAX_SESSION_ITERATIONS,
                str(admin_session.admin_id),
            )
            if preceding_session_id is not None:
                pipeline.delete(self.generate_admin_session_name(preceding_session_id))
            pipeline.hset(
                self.admin_last_auth_activity_map_name,
                str(admin_session.admin_id),
                datetime.fromtimestamp(
                    admin_session.epoch_timestamp, tz=UTC
                ).isoformat(),
            )
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

    async def terminate_session(
        self,
        session_id: str,
        admin_id: int,
        *,
        termination_timestamp: int | None = None,
    ) -> None:
        # Technically speaking, the OR on termination_timestamp overrides a value of 0
        # Luckily we do not serve admins from 1st January 1970
        async with self.session_store.pipeline() as pipeline:
            pipeline.delete(self.generate_admin_session_name(session_id))
            pipeline.hdel(self.admin_session_reverse_mapping_name, str(admin_id))
            pipeline.hset(
                self.admin_last_auth_activity_map_name,
                str(admin_id),
                datetime.fromtimestamp(
                    termination_timestamp or int(time.time()), tz=UTC
                ).isoformat(),
            )
            await pipeline.execute()

    async def terminate_malformed_session(
        self,
        session_name: str,
        *,
        admin_id: int | None = None,
        termination_timestamp: int | None = None,
    ) -> None:
        async with self.session_store.pipeline() as pipeline:
            pipeline.delete(session_name)
            if admin_id:
                pipeline.hdel(self.admin_session_reverse_mapping_name, str(admin_id))
                pipeline.hset(
                    self.admin_last_auth_activity_map_name,
                    str(admin_id),
                    datetime.fromtimestamp(
                        termination_timestamp or int(time.time()), tz=UTC
                    ).isoformat(),
                )
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

    async def get_admin_latest_auth_timestamp_records(self) -> dict[int, datetime]:
        records: dict[str, str] = await self.session_store.hgetall(  # pyrefly:ignore[not-async,bad-assignment]
            self.admin_last_auth_activity_map_name
        )
        return {
            int(k): datetime.fromtimestamp(int(v), tz=UTC) for k, v in records.items()
        }

    @staticmethod
    def _serialize_auth_activity_timestamps(
        timestamps: Mapping[int, datetime],
    ) -> tuple[str, ...]:
        return tuple(
            i
            for author_id, ts in timestamps.items()
            for i in (str(author_id), str(ts.timestamp()))
        )

    async def evict_auth_timestamp_records(self, timestamps: Mapping[int, datetime]):
        async with self.session_store.pipeline(transaction=True) as pipeline:
            pipeline.evalsha(
                self.script_registry.registered_script_hashes[
                    RedisScriptName.CONDITIONAL_HASH_ENTRY_EVICTION_SCRIPT
                ],
                1,
                self.admin_last_auth_activity_map_name,  # KEYS[1]
                *self._serialize_auth_activity_timestamps(timestamps),
            )
