from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Final

from sqlalchemy.ext.asyncio.session import AsyncSession, async_sessionmaker

from auxillary.mixins.abstract import StrictAbstractMixin
from auxillary.mixins.metaclass import AntiSingletonMixin


@dataclass(slots=True, weakref_slot=False)
class AbstractRepository(StrictAbstractMixin, abstract=True):
    """
    Basic skeletol repository to contain
    """

    session_maker: Final[async_sessionmaker[AsyncSession]]


### NOTE ###
# AbstractWorkRepository should never be made into a singleton
# (such as through functools.lru_cache, __new__, or a metaclass),
# since they make use of instance-level state to make decisions
# on session commitment
### END ###


@dataclass(slots=True, weakref_slot=False)
class AbstractWorkRepository(AbstractRepository, AntiSingletonMixin, abstract=True):
    _work_session: ContextVar[AsyncSession | None] = field(
        init=False,
        default_factory=lambda: ContextVar[AsyncSession | None](
            "repo_work_session", default=None
        ),
    )

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
    async def unit_of_work(self):
        async with self.session_maker() as session:
            async with self._unit_of_work(session, True):
                yield

    @asynccontextmanager
    async def external_unit_of_work(self, session: AsyncSession):
        async with self._unit_of_work(session, False):
            yield

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
