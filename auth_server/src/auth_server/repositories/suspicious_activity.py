"""Data access repository for Keydata SA model"""

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from sqlalchemy import insert, select

from auxillary.data_structures.dto import AbstractResult
from auxillary.data_structures.repository import AbstractWorkRepository

from auth_server.models.database import SuspiciousActivity


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


@dataclass(slots=True)
class SuspiciousActivityRepository(AbstractWorkRepository):
    async def get_activity_log(
        self, admin_id: int, limit: int | None = None
    ) -> list[SuspiciousActivityResult]:
        async with self._work_scoped_session() as session:
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
        async with self._work_scoped_session() as session:
            await session.execute(
                insert(SuspiciousActivity).values(
                    suspect=admin_id, description=description, time_logged=time_logged
                )
            )
