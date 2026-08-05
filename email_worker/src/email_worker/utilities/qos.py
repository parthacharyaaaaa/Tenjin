from typing import MutableSequence, Sequence

from aiosmtplib import SMTPException
from resource_auxillary.events import StreamedEvent

from email_worker.src.email_worker.utilities.parsing import parse_user_email_payload


def determine_smtp_error_threshold_reached(
    smtp_error_data: Sequence[tuple[SMTPException, float]],
    timeframe_size: float | int,
    error_threshold: int,
) -> bool:
    if len(smtp_error_data) < error_threshold:
        return False
    timeframe_start, timeframe_end = (
        smtp_error_data[0][1],
        smtp_error_data[error_threshold][1],
    )
    for error_entry in range(0, len(smtp_error_data), error_threshold):
        timeframe_start, timeframe_end = (
            smtp_error_data[error_entry][1],
            smtp_error_data[error_entry + error_threshold][1],
        )
        if timeframe_end - timeframe_start <= timeframe_size:
            return True
    return False


def clean_user_email_payloads(
    events: list[StreamedEvent], improper_events_buffer: MutableSequence[StreamedEvent]
) -> None:
    for i, event in enumerate(events.copy()):
        try:
            parse_user_email_payload(event)
        except Exception:
            improper_events_buffer.append(events.pop(i))
