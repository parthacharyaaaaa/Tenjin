from collections.abc import Callable
from types import MappingProxyType
from typing import Final

from resource_auxillary.strings import EventName, StreamName

from email_worker.tasks.stream_reading import batch_dispatcher

STREAM_EVENT_MAPPING: Final[MappingProxyType[EventName, StreamName]] = MappingProxyType(
    {
        EventName.USER_DELETION_EMAIL: StreamName.USER_EMAILS,
        EventName.USER_REGISTRATION_EMAIL: StreamName.USER_EMAILS,
        EventName.USER_PASSWORD_RECOVERY_EMAIL: StreamName.USER_EMAILS,
    }
)

STREAM_CONSUMER_MAPPING: Final[MappingProxyType[StreamName, Callable]] = (
    MappingProxyType(
        {
            StreamName.USER_EMAILS: batch_dispatcher,
        }
    )
)
