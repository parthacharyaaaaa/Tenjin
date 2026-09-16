from contextlib import asynccontextmanager, AsyncExitStack

from auxillary.data_structures.repository import (
    AbstractWorkRepository,
    AbstractRepository,
)


class MultiRepositoryWorkCoordinator(AbstractRepository):
    @asynccontextmanager
    async def multirepo_work_context(self, *repositories: AbstractWorkRepository):
        async with self.session_maker() as session:
            try:
                async with AsyncExitStack() as stack:
                    for repository in repositories:
                        await stack.enter_async_context(
                            repository.external_unit_of_work(session)
                        )
                    yield
            except Exception:
                raise
            else:
                await session.commit()
