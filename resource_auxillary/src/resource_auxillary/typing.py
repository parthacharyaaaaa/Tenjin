from typing import Protocol


class SupportsExponentialJitteredRetryPolicy(Protocol):
    MAX_RETRIES: int
    MAXIMUM_BACKOFF_INTERVAL: int
    BASE_BACKOFF_INTERVAL: int
    BACKOFF_EXPONENTIAL: int


class SupportsInternalQueueConsumerPolicy(Protocol):
    IQ_CONSUMER_BASE_WAITING_TIME: int
    IQ_CONSUMER_GET_TIMEOUT: int
    IQ_CONSUMER_BATCH_SIZE_QUOTA: int
    IQ_CONSUMER_SLEEP_INTERVAL: int
