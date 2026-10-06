import pytest


@pytest.mark.asyncio
async def test_abstract_work_repository_preserves_work_scoped_session(
    test_repo,
) -> None:
    async with test_repo.unit_of_work():
        await test_repo.work_scoped_operation()
        await test_repo.work_scoped_operation()
    assert test_repo.references[0] == test_repo.references[1]


@pytest.mark.asyncio
async def test_abstract_work_repository_propagates_error(test_repo) -> None:
    with pytest.raises(ValueError, match="foo"):
        async with test_repo.unit_of_work():
            raise ValueError("foo")


@pytest.mark.asyncio
async def test_abstract_work_repository_commitment(test_repo_and_session) -> None:
    test_repo, session = test_repo_and_session
    async with test_repo.unit_of_work():
        pass
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_abstract_work_repository_external_session_commitment(
    test_repo_and_session,
) -> None:
    test_repo, session = test_repo_and_session
    async with test_repo.external_unit_of_work(session):
        pass
    session.commit.assert_not_awaited()
