"""Data access repository for Keydata SA model"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, overload

import ecdsa

from sqlalchemy import insert, select, update
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

    @overload
    def insert_keydata(
        self,
        key_id: str,
        private_key: ecdsa.SigningKey,
        public_key: ecdsa.VerifyingKey,
        alg: str,
        curve: ecdsa.curves.Curve,
        epoch: datetime | None = None,
        *,
        returning: Literal[False],
    ) -> None: ...

    @overload
    def insert_keydata(
        self,
        key_id: str,
        private_key: ecdsa.SigningKey,
        public_key: ecdsa.VerifyingKey,
        alg: str,
        curve: ecdsa.curves.Curve,
        epoch: datetime | None = None,
        *,
        returning: Literal[True],
    ) -> KeyData: ...

    def insert_keydata(
        self,
        key_id: str,
        private_key: ecdsa.SigningKey,
        public_key: ecdsa.VerifyingKey,
        alg: str,
        curve: ecdsa.curves.Curve,
        epoch: datetime | None = None,
        *,
        returning: bool = False,
    ) -> KeyData | None:
        with self.session_maker() as session:
            keydata: KeyData = session.execute(
                insert(KeyData)
                .values(
                    kid=key_id,
                    alg=alg,
                    curve=str(curve),
                    epoch=epoch or datetime.now(),
                    private_pem=private_key.to_pem(),
                    public_pem=public_key.to_pem(),
                )
                .returning(KeyData)
            ).scalar_one()

            session.commit()

            if returning:
                return keydata

    @overload
    def expire_keydata(
        self,
        threshold: datetime,
        expiry_time: datetime | None = None,
        *,
        return_expired: Literal[False],
    ) -> None: ...
    @overload
    def expire_keydata(
        self,
        threshold: datetime,
        expiry_time: datetime | None = None,
        *,
        return_expired: Literal[True],
    ) -> list[KeyData]: ...

    def expire_keydata(
        self,
        threshold: datetime,
        expiry_time: datetime | None = None,
        *,
        return_expired: bool = False,
    ) -> list[KeyData] | None:
        with self.session_maker() as session:
            expired_keys: list[KeyData] = list(
                session.execute(
                    update(KeyData)
                    .where(KeyData.epoch < threshold)
                    .values(expired_at=expiry_time or datetime.now())
                    .returning(KeyData)
                )
                .scalars()
                .all()
            )

            session.commit()

            if return_expired:
                return expired_keys
