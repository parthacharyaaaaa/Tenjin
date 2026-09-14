"""Data access repository for Keydata SA model"""

from auth_server.models.database import SuspiciousActivity
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from auxillary.data_structures.dto import AbstractResult

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio.session import AsyncSession, async_sessionmaker


@dataclass(slots=True, init=False)
class SuspiciousActivityResult(AbstractResult):
    """
    DTO for SuspiciousActivity ORM object
    """

    resource_name: ClassVar[str] = SuspiciousActivity.__tablename__

    id_: int
    suspect: int
    time_logged: datetime
    description: str


@dataclass(frozen=True, slots=True, weakref_slot=True)
class SuspiciousActivityRepository:
    session_maker: async_sessionmaker[AsyncSession]

    async def get_activity_log(
        self, admin_id: int, limit: int | None = None
    ) -> list[SuspiciousActivityResult]:
        async with self.session_maker() as session:
            results: list[SuspiciousActivity] = list(
                (
                    await session.execute(
                        select(SuspiciousActivity)
                        .where(SuspiciousActivity.suspect == admin_id)
                        .limit(limit)
                        .order_by(SuspiciousActivity.time_logged.desc())
                    )
                )
                .scalars()
                .all()
            )

            return list(map(SuspiciousActivityResult.construct_from_orm, results))

    async def insert_activity(
        self, admin_id: int, description: str, *, time_logged: datetime | None = None
    ) -> None:
        async with self.session_maker() as session:
            await session.execute(
                insert(SuspiciousActivity).values(
                    suspect=admin_id, description=description, time_logged=time_logged
                )
            )
            await session.commit()
