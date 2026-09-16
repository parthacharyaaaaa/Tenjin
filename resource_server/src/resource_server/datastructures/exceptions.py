class ResourceNotFoundError(Exception):
    pass


class ResourceDeletedError(Exception):
    pass


class OperationUnderwayError(Exception):
    pass


class CacheCoherenceError(Exception):
    pass


class DuplicateRequestError(Exception):
    pass


class ConflictingIntentError(Exception):
    pass
