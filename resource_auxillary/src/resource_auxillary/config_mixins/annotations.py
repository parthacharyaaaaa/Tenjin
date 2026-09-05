from datetime import timedelta
from typing import Annotated

from pydantic import BeforeValidator

from resource_auxillary.config_mixins.validators import (
    milliseconds_to_timedelta_validator,
)

timedelta_ms = Annotated[
    timedelta, BeforeValidator(milliseconds_to_timedelta_validator)
]
