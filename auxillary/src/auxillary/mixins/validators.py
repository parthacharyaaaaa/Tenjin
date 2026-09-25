from datetime import timedelta


def _generic_time_check(t: int) -> None:
    if t < 0:
        raise ValueError("Negative time value")


def milliseconds_to_timedelta_validator(ms: int) -> timedelta:
    _generic_time_check(ms)
    return timedelta(milliseconds=ms)


def seconds_to_timedelta_validator(s: int) -> timedelta:
    _generic_time_check(s)
    return timedelta(seconds=s)


def days_to_timedelta_validator(d: int) -> timedelta:
    _generic_time_check(d)
    return timedelta(days=d)
