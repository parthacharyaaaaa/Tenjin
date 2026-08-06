from types import MappingProxyType
from typing import Final

from resource_auxillary.strings import EventName, StreamName

STREAM_EVENT_MAPPING: Final[MappingProxyType[EventName, StreamName]] = MappingProxyType(
    {
        EventName.USER_DELETION_EMAIL: StreamName.USER_EMAILS,
        EventName.USER_REGISTRATION_EMAIL: StreamName.USER_EMAILS,
        EventName.USER_PASSWORD_RECOVERY_EMAIL: StreamName.USER_EMAILS,
    }
)
