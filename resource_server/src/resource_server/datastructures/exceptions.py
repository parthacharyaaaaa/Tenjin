class ResourceNotFoundException(Exception):
    pass


class ResourceDeletedException(Exception):
    pass


class OperationUnderwayException(Exception):
    pass


class CacheCoherenceException(Exception):
    pass
