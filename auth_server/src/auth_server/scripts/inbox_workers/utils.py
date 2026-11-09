import asyncio
import logging
import random
import traceback
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final, TypeVar

from auth_server.models.data_transport.inbox import InboxEventResult
from auth_server.models.database import EventDelivery, InboxDeadLetter, InboxTable
from auth_server.models.database_enums import InboxErrorTypes
from auth_server.models.events.enums import EventSubtype
from sqlalchemy.ext.asyncio.session import AsyncSession, async_sessionmaker
from sqlalchemy.sql.expression import insert, select, update

T = TypeVar("T")


async def claim_latest_entry(
    session_maker: async_sessionmaker[AsyncSession],
    sub_event_type: EventSubtype,
    claim_time: datetime,
    leaser_identity: str,
    lease_duration: timedelta,
    max_attempts: int,
) -> InboxEventResult | None:
    async with session_maker() as session:
        # Further SELECTION on lease_expiry is omitted to not
        # mess with DESC index ordering
        latest_updation_announcemenet_event: EventDelivery | None = (
            await session.execute(
                select(EventDelivery)
                .where(
                    (EventDelivery.processed_at.is_(None))
                    & (EventDelivery.supersession_message_id.is_(None))
                    & (EventDelivery.subscriber_type == sub_event_type)
                    & (EventDelivery.attempts < max_attempts)
                )
                .order_by(EventDelivery.message_id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest_updation_announcemenet_event is None or (
            latest_updation_announcemenet_event.lease_expiry_at is not None
            and latest_updation_announcemenet_event.lease_expiry_at >= claim_time
        ):
            return None

        # Optimistic lock
        latest_updation_announcemenet_event = (
            await session.execute(
                update(EventDelivery)
                .where(
                    (
                        EventDelivery.message_id
                        == latest_updation_announcemenet_event.message_id
                    )
                    & (
                        EventDelivery.lease_token
                        == latest_updation_announcemenet_event.lease_token
                    )
                )
                .values(
                    lease_token=leaser_identity,
                    last_attempt_at=claim_time,
                    lease_expiry_at=claim_time + lease_duration,
                    attempts=EventDelivery.attempts + 1,
                )
                .returning(EventDelivery)
            )
        ).scalar_one_or_none()
        result = (
            InboxEventResult.construct_from_orm(latest_updation_announcemenet_event)
            if latest_updation_announcemenet_event
            else None
        )
        await session.commit()
    return result


async def acknowledge_event(
    session_maker: async_sessionmaker[AsyncSession],
    sub_event_type: EventSubtype,
    message_id: int,
    acknowledgement_time: datetime,
    leaser_identity: str,
) -> bool:
    async with session_maker() as session:
        # Order of WHERE clauses is based on index "ix_pending_inbox_id"
        acknowledged_event: Final[EventDelivery | None] = (
            await session.execute(
                update(EventDelivery)
                .where(
                    (EventDelivery.processed_at.is_(None))
                    & (EventDelivery.supersession_message_id.is_(None))
                    & (EventDelivery.message_id == message_id)
                    & (EventDelivery.lease_token == leaser_identity)
                )
                .values(processed_at=acknowledgement_time)
                .returning(EventDelivery)
            )
        ).scalar_one_or_none()

        # Supercession only if event was succesfully ACK'd
        if not acknowledged_event:
            await session.rollback()  # This is virtually pointless, no?
            return False

        # Superceed older key-state records that have been made outdated
        # thanks to this record's processing
        await session.execute(
            update(EventDelivery)
            .where(
                (EventDelivery.processed_at.is_(None))
                & (EventDelivery.supersession_message_id.is_(None))
                & (EventDelivery.message_id < message_id)
                & (EventDelivery.subscriber_type == sub_event_type)
            )
            .values(supersession_message_id=message_id)
        )
        await session.commit()
    return True


async def acknowledge_event_with_logging(
    session_maker: async_sessionmaker[AsyncSession],
    leased_event: InboxEventResult,
    leaser_identity: str,
    logger: logging.Logger,
    acknowledgement_time: datetime | None = None,
) -> bool:
    acknowledgement_time = acknowledgement_time or datetime.now(tz=UTC)
    ack_result: bool = await acknowledge_event(
        session_maker,
        leased_event.subscriber_type,
        leased_event.message_id,
        acknowledgement_time,
        leaser_identity,
    )
    if ack_result:
        logger.info(
            f"Processed message: {leased_event.message_id} at {acknowledgement_time.isoformat()}."
        )
        return True
    logger.info(
        f"Message ownership changed, failed to acknowledge: {leased_event.message_id}"
    )
    return False


async def update_error(
    session_maker: async_sessionmaker[AsyncSession],
    message: InboxEventResult,
    exception: Exception,
    exception_type: InboxErrorTypes = InboxErrorTypes.GENERAL_PROCESSING_ERROR,
    *,
    expire_event: bool = False,
) -> bool:
    error_text: Final[str] = "".join(traceback.format_exception(exception, chain=True))
    async with session_maker() as session:
        result: Final[int | None] = (
            await session.execute(
                update(EventDelivery)
                .where(
                    (EventDelivery.message_id == message.message_id)
                    & (EventDelivery.lease_token == message.lease_token)
                )
                .values(last_error_text=error_text)
                .returning(EventDelivery.message_id)
            )
        ).scalar_one_or_none()

        if result is not None and expire_event:
            await session.execute(
                insert(InboxDeadLetter).values(
                    message_id=message.message_id,
                    error_text=error_text,
                    last_attempt=message.last_attempt_at,
                    attempt_count=message.attempts,
                    event_type=message.subscriber_type,
                    error_type=exception_type,
                )
            )
        await session.commit()
    return result is not None


async def fetch_event_payload(
    session_maker: async_sessionmaker[AsyncSession],
    message_id: int,
    payload_validation_callable: Callable[[dict[str, Any]], T],
) -> T:
    async with session_maker() as session:
        raw_payload: Final[dict[str, Any]] = (
            await session.execute(
                select(InboxTable.payload).where((InboxTable.message_id == message_id))
            )
        ).scalar_one()
    return payload_validation_callable(raw_payload)


async def random_wait(min: timedelta, max: timedelta) -> None:
    if min > max:
        raise ValueError(f"Lower bound {min} greater than upper bound {max}")
    await asyncio.sleep(
        random.uniform(min.total_seconds(), max.total_seconds())  # nosec
    )
