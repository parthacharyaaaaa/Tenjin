from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from auth_server.admin.roles import AdminRole
from auth_server.models.database import Admin
from auth_server.repositories.admin import AdminRepository
from auth_server.strings import SelectionLockOption
from freezegun import freeze_time
from sqlalchemy import insert
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import select, text, update
from sqlalchemy.util.typing import Final, LiteralString

TEST_ACTIVITY_COUNT: Final[int] = 5
TEST_ADMIN_ID: Final[int] = 1
TEST_ADMIN_NAME: Final[LiteralString] = "test_foobar_admin"

DUMMY_DATETIME: Final[datetime] = datetime(1, 1, 1, tzinfo=UTC)


@pytest_asyncio.fixture(scope="module", autouse=True)
async def seed_db(pg_async_sessionmaker: async_sessionmaker[AsyncSession]):
    async with pg_async_sessionmaker() as session:
        await session.execute(
            insert(Admin).values(
                id_=TEST_ADMIN_ID,
                username=TEST_ADMIN_NAME,
                role=AdminRole.SUPER,
                password_hash=b"x" * 32,
                created_by=TEST_ADMIN_ID,
            )
        )
        await session.execute(
            text("""
                SELECT setval(
                    pg_get_serial_sequence('admins', 'id_'),
                    COALESCE((SELECT MAX(id_) FROM admins), 1),
                    EXISTS (SELECT 1 FROM admins)
                )
            """)
        )
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identity",
    (
        TEST_ADMIN_ID,
        TEST_ADMIN_NAME,
    ),
)
async def test_admin_retrieval(
    admin_repository: AdminRepository, identity: str | int
) -> None:
    if isinstance(identity, int):
        assert await admin_repository.get_admin(identity) is not None
    else:
        assert await admin_repository.get_admin_by_username(identity) is not None


@pytest.mark.asyncio
async def test_deleted_admin_retrieval(
    admin_repository: AdminRepository,
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with pg_async_sessionmaker() as session:
        async with admin_repository.external_unit_of_work(session):
            await session.execute(
                update(Admin)
                .where(Admin.id_ == TEST_ADMIN_ID)
                .values(time_deleted=datetime.now(tz=UTC))
            )
            assert (
                await admin_repository.get_admin(TEST_ADMIN_ID, include_deleted=True)
                is not None
            )
            assert (
                await admin_repository.get_admin(TEST_ADMIN_ID, include_deleted=False)
                is None
            )


@pytest.mark.asyncio
async def test_admin_deletion(
    admin_repository: AdminRepository,
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with pg_async_sessionmaker() as session:
        async with admin_repository.external_unit_of_work(session):
            await admin_repository.delete_admin(TEST_ADMIN_ID)
            assert (
                await admin_repository.get_admin(TEST_ADMIN_ID, include_deleted=True)
                is not None
            )


@pytest.mark.asyncio
async def test_admin_repeated_deletion(
    admin_repository: AdminRepository,
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with pg_async_sessionmaker() as session:
        async with admin_repository.external_unit_of_work(session):
            await admin_repository.delete_admin(TEST_ADMIN_ID)
            assert (
                await admin_repository.delete_admin(TEST_ADMIN_ID, returning=True)
                is None
            )


@pytest.mark.asyncio
async def test_locking(
    admin_repository: AdminRepository,
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with pg_async_sessionmaker() as session:
        async with admin_repository.unit_of_work():
            await admin_repository.get_admin(
                TEST_ADMIN_ID, lock_args=(SelectionLockOption.READ,)
            )
            locked_admin: Admin | None = (
                await session.execute(
                    select(Admin)
                    .where(Admin.id_ == TEST_ADMIN_ID)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()

            assert locked_admin is None


@pytest.mark.asyncio
@pytest.mark.parametrize("lock", (True, False))
async def test_admin_locking(
    admin_repository: AdminRepository,
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
    lock: bool,
) -> None:
    async with pg_async_sessionmaker() as session:
        async with admin_repository.external_unit_of_work(session):
            await admin_repository.set_admin_locked(TEST_ADMIN_ID, locked=lock)

            assert lock == (await admin_repository.get_admin(TEST_ADMIN_ID)).locked  # type: ignore


@pytest.mark.asyncio
async def test_admin_insertion(
    admin_repository: AdminRepository,
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with pg_async_sessionmaker() as session:
        async with admin_repository.external_unit_of_work(session):
            await admin_repository.create_admin(
                username="foobar",
                password_hash=b"x" * 32,
                role=AdminRole.STAFF,
                creation_author=TEST_ADMIN_ID,
            )
            assert await admin_repository.get_admin_by_username("foobar") is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("login_time", (DUMMY_DATETIME, None))
@freeze_time(DUMMY_DATETIME)
async def test_last_login_updation(
    admin_repository: AdminRepository,
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
    login_time: datetime | None,
) -> None:
    async with pg_async_sessionmaker() as session:
        async with admin_repository.external_unit_of_work(session):
            await admin_repository.update_last_login(TEST_ADMIN_ID, login_time)
            assert (  # type: ignore
                await admin_repository.get_admin(TEST_ADMIN_ID)
            ).last_login == DUMMY_DATETIME


INVALID_ADMIN_KWARGS: tuple[dict[str, Any], ...] = (
    {"username": "foo"},
    {"role": "__NON_EXISTENT__"},
    {"creation_author": -1},
    {},
)


@pytest.mark.asyncio
@pytest.mark.parametrize("incorrect_kwargs", INVALID_ADMIN_KWARGS)
async def test_admin_creation_integrity(
    admin_repository: AdminRepository,
    pg_async_sessionmaker: async_sessionmaker[AsyncSession],
    incorrect_kwargs: dict[str, Any],
) -> None:
    default_insertion_kwargs: dict[str, Any] = {
        "username": "foobar",
        "password_hash": b"x" * 32,
        "role": AdminRole.STAFF,
        "creation_author": TEST_ADMIN_ID,
    }
    async with pg_async_sessionmaker() as session:
        async with admin_repository.external_unit_of_work(session):
            if not incorrect_kwargs:
                await admin_repository.create_admin(
                    **(default_insertion_kwargs | incorrect_kwargs)
                )
                assert (
                    await admin_repository.get_admin_by_username("foobar") is not None
                )
            else:
                with pytest.raises((IntegrityError, DataError)):  # noqa
                    await admin_repository.create_admin(
                        **(default_insertion_kwargs | incorrect_kwargs)
                    )
