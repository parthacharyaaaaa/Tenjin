import asyncio
import random
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Final

from auth_server.config import AdminConfigModel
from auth_server.models.database import Admin
from auth_server.subsystems.session_manager import AdminSessionManager
from auxillary.data_structures.locks.typing import (
    SupportsBasicLockContext,
    SupportsDistributedLocking,
)
from sqlalchemy.ext.asyncio.session import AsyncSession, async_sessionmaker
from sqlalchemy.sql.expression import column, update, values
from sqlalchemy.types import BIGINT, TIMESTAMP


async def admin_auth_time_writeback(
    session_maker: async_sessionmaker[AsyncSession],
    auth_activity_info: Mapping[int, datetime],
) -> None:
    data = values(
        column("id_", BIGINT), column("last_auth_activity_at", TIMESTAMP(timezone=True))
    ).data(tuple((k, v) for k, v in auth_activity_info.items()))
    async with session_maker() as session:
        await session.execute(
            update(Admin)
            .where(
                (Admin.id_ == data.c.id_)
                & (Admin.last_auth_activity_at < data.c.last_auth_activity_at)
            )
            .values(last_auth_activity_at=data.c.last_auth_activity_at)
        )
        await session.commit()
    return


async def coordinated_admin_auth_time_writeback(
    session_maker: async_sessionmaker[AsyncSession],
    session_manager: AdminSessionManager,
    lock_manager: SupportsDistributedLocking,
    admin_config: AdminConfigModel,
) -> None:
    while True:
        auth_activity_info: Final[MappingProxyType[int, datetime]] = MappingProxyType(
            await session_manager.get_admin_latest_auth_timestamp_records()
        )
        lock: SupportsBasicLockContext = await lock_manager.lock(
            admin_config.LATEST_AUTH_ACTIIVTY_WRITEBACK_LOCK_NAME,
            admin_config.LATEST_AUTH_ACTIIVTY_WRITEBACK_LOCK_VALUE,
            int(
                admin_config.LATEST_AUTH_ACTIIVTY_WRITEBACK_LOCK_DURATION.total_seconds()
            ),
        )
        if lock.valid:
            async with lock:
                await admin_auth_time_writeback(session_maker, auth_activity_info)
            await session_manager.evict_auth_timestamp_records(auth_activity_info)

        await asyncio.sleep(
            random.uniform(  # nosec
                admin_config.LATEST_AUTH_ACTIIVTY_WRITEBACK_MIN_INTERVAL.total_seconds(),
                admin_config.LATEST_AUTH_ACTIIVTY_WRITEBACK_MAX_INTERVAL.total_seconds(),
            )
        )
