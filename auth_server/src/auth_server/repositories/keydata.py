"""Data access repository for Keydata SA model"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, overload

import ecdsa

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio.session import AsyncSession, async_sessionmaker

from auth_server.models.database import KeyData
from auth_server.utils.singleton import SingletonMetaclass


@dataclass(frozen=True, slots=True, weakref_slot=True)
class KeydataRepository(metaclass=SingletonMetaclass):
    session_maker: async_sessionmaker[AsyncSession]

    async def get_keydata(self, key_id: str) -> KeyData | None:
        async with self.session_maker() as session:
            return (
                await session.execute(select(KeyData).where(KeyData.kid == key_id))
            ).scalar_one_or_none()

    async def get_relevant_keydata(
        self, limit: int | None, raise_on_empty: bool = False
    ) -> list[KeyData]:
        async with self.session_maker() as session:
            keydata: list[KeyData] | None = list(
                (
                    await session.execute(
                        select(KeyData)
                        .where(KeyData.expired_at.is_(None))
                        .order_by(KeyData.epoch.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            if not keydata and raise_on_empty:
                raise ValueError("Key data empty")
            return keydata

    @overload
    async def insert_keydata(
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
    async def insert_keydata(
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

    async def insert_keydata(
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
        async with self.session_maker() as session:
            keydata: KeyData = (
                await session.execute(
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
                )
            ).scalar_one()

            await session.commit()

            if returning:
                return keydata

    @overload
    async def expire_keydata(
        self,
        threshold: datetime,
        expiry_time: datetime | None = None,
        *,
        return_expired: Literal[False],
    ) -> None: ...
    @overload
    async def expire_keydata(
        self,
        threshold: datetime,
        expiry_time: datetime | None = None,
        *,
        return_expired: Literal[True],
    ) -> list[KeyData]: ...

    async def expire_keydata(
        self,
        threshold: datetime,
        expiry_time: datetime | None = None,
        *,
        return_expired: bool = False,
    ) -> list[KeyData] | None:
        async with self.session_maker() as session:
            expired_keys: list[KeyData] = list(
                (
                    await session.execute(
                        update(KeyData)
                        .where(KeyData.epoch < threshold)
                        .values(expired_at=expiry_time or datetime.now())
                        .returning(KeyData)
                    )
                )
                .scalars()
                .all()
            )

            await session.commit()

            if return_expired:
                return expired_keys

    async def get_expired_keys(self) -> list[KeyData]:
        async with self.session_maker() as session:
            return list(
                (
                    await session.execute(
                        select(KeyData)
                        .where(KeyData.expired_at.isnot_(None))
                        .order_by(KeyData.expired_at)
                    )
                )
                .scalars()
                .all()
            )
