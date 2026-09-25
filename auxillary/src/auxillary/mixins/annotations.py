from datetime import timedelta
from typing import Annotated

from pydantic import BeforeValidator

from auxillary.mixins.validators import (
    days_to_timedelta_validator,
    milliseconds_to_timedelta_validator,
    seconds_to_timedelta_validator,
)

timedelta_ms = Annotated[
    timedelta, BeforeValidator(milliseconds_to_timedelta_validator)
]

timedelta_s = Annotated[timedelta, BeforeValidator(seconds_to_timedelta_validator)]

timedelta_days = Annotated[timedelta, BeforeValidator(days_to_timedelta_validator)]
