from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from auxillary.data_structures.uow import MultiRepositoryWorkCoordinator


@pytest.mark.asyncio
async def test_error_propagation(
    test_repo, multirepo_coordinator: MultiRepositoryWorkCoordinator
) -> None:
    with pytest.raises(RuntimeError):
        async with multirepo_coordinator.multirepo_work_context(test_repo):
            raise RuntimeError()


@pytest.mark.asyncio
async def test_commitment(test_repo_factory) -> None:
    session = AsyncMock()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session

    multirepo_coordinator = MultiRepositoryWorkCoordinator(
        MagicMock(return_value=session_context)
    )

    async with multirepo_coordinator.multirepo_work_context(test_repo_factory()):
        pass
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_context_stacking(
    test_repo_factory, multirepo_coordinator: MultiRepositoryWorkCoordinator
) -> None:
    first_repo, second_repo = test_repo_factory(), test_repo_factory()

    calls = []

    @asynccontextmanager
    async def first_context(session):
        calls.append("enter first")
        try:
            yield
        finally:
            calls.append("exit first")

    @asynccontextmanager
    async def second_context(session):
        calls.append("enter second")
        try:
            yield
        finally:
            calls.append("exit second")

    first_repo.external_unit_of_work = first_context
    second_repo.external_unit_of_work = second_context

    async with multirepo_coordinator.multirepo_work_context(first_repo, second_repo):
        calls.append("body")

    assert calls == [
        "enter first",
        "enter second",
        "body",
        "exit second",
        "exit first",
    ]
