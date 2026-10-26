from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar, Token
from typing import Final, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from auxillary.data_structures.repository import (
    AbstractRepository,
)


class SupportsExternalUnitOfWork(Protocol):
    _work_session: ContextVar[AsyncSession | None]

    @asynccontextmanager
    async def _unit_of_work(self, session: AsyncSession, autocommit: bool):
        token: Final[Token[AsyncSession | None]] = self._work_session.set(session)
        try:
            yield
        except Exception:
            raise
        else:
            if autocommit:
                await session.commit()
        finally:
            self._work_session.reset(token)

    @asynccontextmanager
    async def external_unit_of_work(self, session: AsyncSession):
        async with self._unit_of_work(session, False):
            yield


class MultiRepositoryWorkCoordinator(AbstractRepository):
    @asynccontextmanager
    async def external_work_context(self, *workers: SupportsExternalUnitOfWork):
        async with self.session_maker() as session:
            try:
                async with AsyncExitStack() as stack:
                    for worker in workers:
                        await stack.enter_async_context(
                            worker.external_unit_of_work(session)
                        )
                    yield
            except Exception:
                raise
            else:
                await session.commit()
