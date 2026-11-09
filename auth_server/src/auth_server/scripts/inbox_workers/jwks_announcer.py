import asyncio
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Final

from auth_server.config.inbox_config import GeneralInboxWorkerConfig
from auth_server.datastructures.exceptions.inbox_error_mapping import (
    INBOX_ERROR_MAPPING,
)
from auth_server.dependencies.local import (
    get_database_session_maker,
    get_inbox_worker_config,
    get_jwks_update_announcer,
)
from auth_server.models.data_transport.inbox import InboxEventResult
from auth_server.models.database_enums import InboxErrorTypes
from auth_server.models.events.enums import EventSubtype
from auth_server.scripts.inbox_workers.strings import (
    STREAM_ID_ORDERING_ERROR_STRING,
    generate_lease_identity,
)
from auth_server.scripts.inbox_workers.utils import (
    acknowledge_event_with_logging,
    claim_latest_entry,
    fetch_event_payload,
    random_wait,
    update_error,
)
from auth_server.subsystems.jwks_announcer import JWKSUpdateAnnouncer
from auxillary.security.data_structures.jwks.models import VariableJWKS
from redis.exceptions import ExceptionType, RedisError, ResponseError
from sqlalchemy.ext.asyncio.session import AsyncSession, async_sessionmaker


async def main() -> None:
    process_id: Final[str] = str(os.getpid())
    logger: Final[logging.Logger] = logging.getLogger(
        generate_lease_identity(process_id)
    )
    logger.debug(f"Initiated logger: {logger.name}")
    config: Final[GeneralInboxWorkerConfig] = get_inbox_worker_config().JWKS_ANNOUNCER
    session_maker: Final[async_sessionmaker[AsyncSession]] = (
        get_database_session_maker()
    )
    update_announcer: Final[JWKSUpdateAnnouncer] = get_jwks_update_announcer()
    logger.debug("Initiated all dependencies")

    while True:
        claim_time: datetime = datetime.now(UTC)
        leaser_identity: str = generate_lease_identity(process_id)
        leased_event: InboxEventResult | None = await claim_latest_entry(
            session_maker,
            EventSubtype.JWKS_UPDATION_ANNOUNCEMENT,
            claim_time,
            leaser_identity,
            config.LEASE_DURATION,
            config.MAX_ATTEMPTS,
        )
        if leased_event:
            logger.info(
                f"Processing leased event: {leased_event.message_id}, attempt: {leased_event.attempts}"
            )
            try:
                jwks_payload: VariableJWKS = await fetch_event_payload(
                    session_maker, leased_event.message_id, VariableJWKS.model_validate
                )
                try:
                    await update_announcer.stream_update(jwks_payload)
                except ResponseError as e:
                    # Specific error-case for idempotency
                    if (
                        e.error_type == ExceptionType.SERVER
                        and e.status_code == "ERR"
                        and next(iter(e.args), None) == STREAM_ID_ORDERING_ERROR_STRING
                    ):
                        await acknowledge_event_with_logging(
                            session_maker,
                            leased_event,
                            leaser_identity,
                            logger,
                        )
            except Exception as e:
                logger.error(f"Error processing {leased_event.message_id}, caught: {e}")
                is_unrecoverable: bool = not (
                    isinstance(e, RedisError) and e.error_type == ExceptionType.NETWORK
                )
                error_type: InboxErrorTypes = next(
                    (
                        INBOX_ERROR_MAPPING[base]
                        for base in type(e).__mro__
                        if base in INBOX_ERROR_MAPPING
                    ),
                    InboxErrorTypes.GENERAL_PROCESSING_ERROR,
                )
                updation_result: bool = await update_error(
                    session_maker,
                    leased_event,
                    e,
                    error_type,
                    expire_event=is_unrecoverable
                    or leased_event.attempts >= config.MAX_ATTEMPTS,
                )
                if not updation_result:
                    logger.info(
                        "Leased event claimed by another worker, failed to update..."
                    )
                if is_unrecoverable:
                    logger.info("Unrecoverabe error, re-raising...")
                    raise
                logger.info("Potentially transient error...")
            else:
                await acknowledge_event_with_logging(
                    session_maker,
                    leased_event,
                    leaser_identity,
                    logger,
                )
        await random_wait(config.BASE_BACKOFF_INTERVAL, config.MAXIMUM_BACKOFF_INTERVAL)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
