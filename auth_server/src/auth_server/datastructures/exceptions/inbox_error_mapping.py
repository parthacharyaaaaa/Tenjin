from json import JSONDecodeError
from types import MappingProxyType
from typing import Final

from pydantic import ValidationError
from redis import exceptions as redis_errors
from sqlalchemy.exc import SQLAlchemyError

from auth_server.models.database_enums import InboxErrorTypes

INBOX_ERROR_MAPPING: Final[MappingProxyType[type[Exception], InboxErrorTypes]] = (
    MappingProxyType(
        {
            redis_errors.TimeoutError: InboxErrorTypes.REDIS_TIMEOUT,
            redis_errors.ConnectionError: InboxErrorTypes.REDIS_NETWORK_FAILURE,
            redis_errors.RedisError: InboxErrorTypes.REDIS_PROCESSING_ERROR,
            SQLAlchemyError: InboxErrorTypes.DATABASE_PROCESSING_ERROR,
            ConnectionError: InboxErrorTypes.GENERAL_NETWORK_FAILURE,
            JSONDecodeError: InboxErrorTypes.MALFORMED_MESSAGE,
            UnicodeDecodeError: InboxErrorTypes.MALFORMED_MESSAGE,
            ValidationError: InboxErrorTypes.MALFORMED_MESSAGE,
        }
    )
)
