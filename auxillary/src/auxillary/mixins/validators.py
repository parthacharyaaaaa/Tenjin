from datetime import timedelta


def milliseconds_to_timedelta_validator(ms: int) -> timedelta:
    if ms < 0:
        raise ValueError("Negative time value")
    return timedelta(milliseconds=ms)


def seconds_to_timedelta_validator(s: int) -> timedelta:
    if s < 0:
        raise ValueError("Negative time value")
    return timedelta(seconds=s)
