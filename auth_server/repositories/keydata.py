"""Data access repository for Keydata SA model"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from auth_server.models.database import KeyData
from auth_server.dependencies import get_database_session_maker
from auth_server.utils.singleton import SingletonMetaclass


@dataclass(frozen=True, slots=True, init=False, weakref_slot=True)
class KeydataRepository(metaclass=SingletonMetaclass):
    session_maker: sessionmaker[Session] = field(
        default_factory=get_database_session_maker, init=False
    )

    def get_keydata(self, key_id: str) -> KeyData | None:
        with self.session_maker() as session:
            return session.execute(
                select(KeyData).where(KeyData.kid == key_id)
            ).scalar_one_or_none()

    def get_relevant_keydata(
        self, limit: int | None, raise_on_empty: bool = False
    ) -> list[KeyData]:
        with self.session_maker() as session:
            keydata: list[KeyData] | None = list(
                session.execute(
                    select(KeyData)
                    .where(KeyData.expired_at.is_(None))
                    .order_by(KeyData.epoch.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            if not keydata and raise_on_empty:
                raise ValueError("Key data empty")
            return keydata
