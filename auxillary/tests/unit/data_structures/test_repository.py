from unittest.mock import AsyncMock, MagicMock

import pytest
from auxillary.data_structures.repository import AbstractWorkRepository


class DummyWorkRepository(AbstractWorkRepository):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.references = []

    async def work_scoped_operation(self) -> None:
        async with self._work_scoped_session() as session:
            self.references.append(id(session))


def _get_test_repo() -> DummyWorkRepository:
    session = AsyncMock()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_maker = MagicMock(return_value=session_context)
    return DummyWorkRepository(session_maker)


def _get_test_repo_with_session() -> tuple[DummyWorkRepository, AsyncMock]:
    session = AsyncMock()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_maker = MagicMock(return_value=session_context)
    return DummyWorkRepository(session_maker), session


@pytest.mark.asyncio
async def test_abstract_work_repository_preserves_work_scoped_session() -> None:
    test_repo = _get_test_repo()
    async with test_repo.unit_of_work():
        await test_repo.work_scoped_operation()
        await test_repo.work_scoped_operation()
    assert test_repo.references[0] == test_repo.references[1]


@pytest.mark.asyncio
async def test_abstract_work_repository_propagates_error() -> None:
    test_repo = _get_test_repo()
    with pytest.raises(ValueError, match="foo"):
        async with test_repo.unit_of_work():
            raise ValueError("foo")


@pytest.mark.asyncio
async def test_abstract_work_repository_commitment() -> None:
    test_repo, session = _get_test_repo_with_session()
    async with test_repo.unit_of_work():
        pass
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_abstract_work_repository_external_session_commitment() -> None:
    test_repo, session = _get_test_repo_with_session()
    async with test_repo.external_unit_of_work(session):
        pass
    session.commit.assert_not_awaited()
