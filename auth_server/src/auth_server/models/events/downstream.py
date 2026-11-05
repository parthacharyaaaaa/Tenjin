from types import MappingProxyType
from typing import Final

from auth_server.models.events.enums import EventSubtype, EventType

EVENT_DOWNSTREAM_MAPPING: Final[
    MappingProxyType[EventType, tuple[EventSubtype, ...]]
] = MappingProxyType(
    {
        EventType.KEY_STORE_WRITE: (
            EventSubtype.JWKS_UPDATION_ANNOUNCEMENT,
            EventSubtype.SYNCED_STORE_UPDATION,
        )
    }
)
