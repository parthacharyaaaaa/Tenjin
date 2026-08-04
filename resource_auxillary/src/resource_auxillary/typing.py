from typing import Protocol


class SupportsExponentialJitteredRetryPolicy(Protocol):
    MAX_RETRIES: int
    MAXIMUM_BACKOFF_INTERVAL: int
    BASE_BACKOFF_INTERVAL: int
    BACKOFF_EXPONENTIAL: int
