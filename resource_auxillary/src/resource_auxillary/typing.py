from datetime import timedelta
from typing import Protocol


class SupportsExponentialJitteredRetryPolicy(Protocol):
    MAX_RETRIES: int
    MAXIMUM_BACKOFF_INTERVAL: timedelta
    BASE_BACKOFF_INTERVAL: timedelta
    BACKOFF_EXPONENTIAL: int


class SupportsInternalQueueConsumerPolicy(Protocol):
    IQ_CONSUMER_BASE_WAITING_TIME: timedelta
    IQ_CONSUMER_GET_TIMEOUT: timedelta
    IQ_CONSUMER_SLEEP_INTERVAL: timedelta
    IQ_CONSUMER_BATCH_SIZE_QUOTA: int


class HasEventID(Protocol):
    event_id: int
