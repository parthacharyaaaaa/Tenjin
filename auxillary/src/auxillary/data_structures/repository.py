from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Final

from sqlalchemy.ext.asyncio.session import async_sessionmaker, AsyncSession

from auxillary.mixins.abstract import StrictAbstractMixin


@dataclass(slots=True, weakref_slot=False)
class AbstractRepository(StrictAbstractMixin, abstract=True):
    """
    Basic skeletol repository to contain
    """

    session_maker: Final[async_sessionmaker[AsyncSession]]


### NOTE ###
# AbstractWorkRepository should never be made into a singleton
# (such as through functools.lru_cache or a metaclass),
# since they make use of instance-level state to make decisions
# on session commitment
### END ###


@dataclass(slots=True, weakref_slot=False)
class AbstractWorkRepository(AbstractRepository, abstract=True):
    _work_session: AsyncSession | None = field(init=False, default=None)

    @asynccontextmanager
    async def unit_of_work(self):
        async with self.session_maker() as session:
            self._work_session = session
            try:
                yield
            except Exception:
                raise
            else:
                await session.commit()
            finally:
                self._work_session = None

    @asynccontextmanager
    async def _work_scoped_session(self):
        if self._work_session is not None:
            yield self._work_session
            return

        async with self.session_maker() as session:
            try:
                yield session
            except Exception:
                raise
            else:
                await session.commit()
