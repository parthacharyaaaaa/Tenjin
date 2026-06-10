from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Callable


class SortOption(StrEnum):
    ASCENDING = "asc"
    DESCENDING = "desc"


class TimeFrameOption(StrEnum):
    LAST_HOUR = "last_hour"
    LAST_DAY = "last_day"
    LAST_WEEK = "last_week"
    LAST_MONTH = "last_month"
    LAST_YEAR = "last_year"
    ALL_TIME = "all_time"


TIMEFRAMES: MappingProxyType[TimeFrameOption, Callable[[datetime], datetime]] = (
    MappingProxyType(
        {
            TimeFrameOption.LAST_HOUR: lambda dt: dt - timedelta(hours=1),
            TimeFrameOption.LAST_DAY: lambda dt: dt - timedelta(days=1),
            TimeFrameOption.LAST_WEEK: lambda dt: dt - timedelta(weeks=1),
            TimeFrameOption.LAST_MONTH: lambda dt: dt - timedelta(days=30),
            TimeFrameOption.LAST_YEAR: lambda dt: dt - timedelta(days=364),
            TimeFrameOption.ALL_TIME: lambda _: datetime.min,
        }
    )
)
