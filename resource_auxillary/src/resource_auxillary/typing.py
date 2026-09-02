from typing import Protocol


class SupportsExponentialJitteredRetryPolicy(Protocol):
    MAX_RETRIES: int
    MAXIMUM_BACKOFF_INTERVAL: float
    BASE_BACKOFF_INTERVAL: float
    BACKOFF_EXPONENTIAL: int


class SupportsInternalQueueConsumerPolicy(Protocol):
    IQ_CONSUMER_BASE_WAITING_TIME: int
    IQ_CONSUMER_GET_TIMEOUT: float
    IQ_CONSUMER_SLEEP_INTERVAL: float
    IQ_CONSUMER_BATCH_SIZE_QUOTA: int


class HasEventID(Protocol):
    event_id: int
