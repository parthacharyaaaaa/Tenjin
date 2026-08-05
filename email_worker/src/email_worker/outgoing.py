"""Outgoing emailiing logic"""

import time
from typing import MutableSequence, Sequence

from aiosmtplib import SMTP, SMTPServerDisconnected
from aiosmtplib.errors import SMTPException, SMTPTimeoutError

from email.message import EmailMessage

from resource_auxillary.events import StreamedEvent
from resource_auxillary.typing import SupportsExponentialJitteredRetryPolicy
from resource_auxillary.coordination import exponential_jittered_backoff

from email_worker.config.email_config import EmailConfig
from email_worker.constants import (
    UNSAFE_SMTP_NETWORK_ERRORS,
)
from email_worker.dependencies import get_fresh_smtp_client
from email_worker.utilities.emails import construct_email_message
from email_worker.utilities.qos import determine_smtp_error_threshold_reached


async def send_email(
    smtp_client: SMTP,
    email_messaage: EmailMessage,
    attempts: int,
    retry_policy: SupportsExponentialJitteredRetryPolicy,
) -> Exception | None:
    for i in range(1, attempts + 1):
        try:
            await smtp_client.send_message(email_messaage)
            return
        except SMTPTimeoutError as e:
            if i == attempts:
                return e
            # Possible lost ACK after sending "."
            # after SMTP server has processed the email on its end
            if isinstance(e, UNSAFE_SMTP_NETWORK_ERRORS):
                return e
            await exponential_jittered_backoff(
                retry_policy.MAXIMUM_BACKOFF_INTERVAL,
                retry_policy.BASE_BACKOFF_INTERVAL,
                i,
                exponential=retry_policy.BACKOFF_EXPONENTIAL,
            )
        except SMTPException as e:
            return e


async def batch_send_emails(
    email_config: EmailConfig,
    smtp_client: SMTP,
    events: Sequence[StreamedEvent],
    attempts: int,
    smtp_error_data: MutableSequence[tuple[SMTPException, float]],
) -> tuple[StreamedEvent, ...]:
    event_count: int = len(events)
    i: int = 0
    successful_sends: list[StreamedEvent] = []
    while i < event_count:
        e: Exception | None = await send_email(
            smtp_client,
            construct_email_message(events[i]),
            attempts,
            email_config.WORKER,
        )
        if e is None:
            i += 1
            successful_sends.append(events[i])
            continue
        else:
            if isinstance(e, SMTPServerDisconnected):
                smtp_client = await get_fresh_smtp_client()
            smtp_error_data.append((e, time.monotonic()))  # type: ignore
            if determine_smtp_error_threshold_reached(
                smtp_error_data,
                email_config.WORKER.SMTP_NETWORK_ERROR_WINDOW,
                email_config.WORKER.MAXIMUM_SMTP_REFRESHES,
            ):
                ...

    return tuple(successful_sends)
