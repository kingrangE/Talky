"""Bounded retry decisions without persisting exception messages or user input."""
import random
from datetime import timedelta


def retry_delay(error, attempt):
    if error == 'worker_interrupted':
        return timedelta(0)
    if isinstance(error, (PermissionError, FileNotFoundError)):
        return None
    if isinstance(error, str):
        # Lease recovery has no original exception; the attempt may have been interrupted.
        retryable = error in {'worker_interrupted', 'TimeoutError', 'ConnectionError', 'OSError', 'RuntimeError'}
    else:
        import httpx
        from sqlalchemy.exc import DBAPIError
        import psycopg
        if isinstance(error, DBAPIError) and (error.connection_invalidated or getattr(error.orig, 'sqlstate', None) in {'40001', '40P01'}):
            return timedelta(seconds=min(30, 2 ** min(attempt, 5)) + random.uniform(0, 1))
        if isinstance(error, (psycopg.OperationalError, psycopg.InterfaceError)):
            return timedelta(seconds=min(30, 2 ** min(attempt, 5)) + random.uniform(0, 1))
        status = getattr(error, 'status_code', None)
        retryable = (isinstance(error, (TimeoutError, ConnectionError, OSError, httpx.TransportError))
                     or status in {408, 429} or isinstance(status, int) and status >= 500)
        cause = getattr(error, '__cause__', None)
        if cause is not None:
            return retry_delay(cause, attempt)
    if not retryable:
        return None
    return timedelta(seconds=min(30, 2 ** min(attempt, 5)) + random.uniform(0, 1))


def error_name(error):
    return error[:128] if isinstance(error, str) else type(error).__name__[:128]
