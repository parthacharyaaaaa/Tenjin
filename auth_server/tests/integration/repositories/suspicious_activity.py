from typing import Final, LiteralString

import pytest
import pytest_asyncio
from auth_server.admin.roles import AdminRole
from auth_server.models.database import Admin, SuspiciousActivity
from auth_server.repositories.suspicious_activity import (
    SuspiciousActivityRepository,
    SuspiciousActivityResult,
)
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio.session import AsyncSession

TEST_ACTIVITY_COUNT: Final[int] = 5
TEST_ADMIN_ID: Final[int] = 1
TEST_ADMIN_NAME: Final[LiteralString] = "test_foobar_admin"
DEFAULT_TEST_ENTRY_TEMPLATE: Final[LiteralString] = "Default activity report: {}"


@pytest_asyncio.fixture(scope="module", autouse=True)
async def seed_db(pg_async_sessionmaker: async_sessionmaker[AsyncSession]):
    async with pg_async_sessionmaker() as session:
        await session.execute(
            insert(Admin).values(
                id_=TEST_ADMIN_ID,
                username=TEST_ADMIN_NAME,
                role=AdminRole.STAFF,
                password_hash=b"x" * 32,
                created_by=TEST_ADMIN_ID,
            )
        )
        for i in range(TEST_ACTIVITY_COUNT):
            await session.execute(
                insert(SuspiciousActivity).values(
                    suspect=TEST_ADMIN_ID,
                    description=DEFAULT_TEST_ENTRY_TEMPLATE.format(i),
                )
            )
        await session.commit()


@pytest.mark.asyncio
async def test_activity_retrieval_order(
    suspicious_activity_repository: SuspiciousActivityRepository,
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with pg_async_sessionmaker() as session:
        async with suspicious_activity_repository.external_unit_of_work(session):
            results: list[
                SuspiciousActivityResult
            ] = await suspicious_activity_repository.get_activity_log(
                admin_id=TEST_ADMIN_ID
            )
            assert len(results) == 5
            assert results == sorted(results, reverse=True, key=lambda x: x.time_logged)
