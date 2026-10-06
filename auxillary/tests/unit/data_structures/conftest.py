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


@pytest.fixture
def test_repo() -> DummyWorkRepository:
    session = AsyncMock()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_maker = MagicMock(return_value=session_context)
    return DummyWorkRepository(session_maker)


@pytest.fixture
def test_repo_and_session() -> tuple[DummyWorkRepository, AsyncMock]:
    session = AsyncMock()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_maker = MagicMock(return_value=session_context)
    return DummyWorkRepository(session_maker), session
