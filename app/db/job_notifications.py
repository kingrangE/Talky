"""Commit-time wakeups for the conversation and exam queues."""

import logging
import time

import psycopg
from sqlalchemy import text

from app.db.postgres import get_engine

log = logging.getLogger(__name__)
CHANNEL = "talky_job_ready"


def notify_jobs(session) -> None:
    session.execute(text(f"NOTIFY {CHANNEL}"))


class JobListener:
    def __init__(self) -> None:
        self.connection: psycopg.Connection | None = None
        self._retry_at = 0.0

    def subscribe(self) -> None:
        if self.connection is not None or time.monotonic() < self._retry_at:
            return
        engine = get_engine()
        args, kwargs = engine.dialect.create_connect_args(engine.url)
        kwargs.update(autocommit=True, connect_timeout=2, options="-c statement_timeout=2000")
        try:
            self.connection = psycopg.connect(*args, **kwargs)
            self.connection.execute(f"LISTEN {CHANNEL}")
        except psycopg.Error as exc:
            self.close()
            self._retry_at = time.monotonic() + 2
            log.warning("Job listener unavailable (%s)", type(exc).__name__)

    def wait(self, timeout: float) -> None:
        assert self.connection is not None
        try:
            for _ in self.connection.notifies(timeout=timeout, stop_after=1):
                pass
        except psycopg.Error as exc:
            self.close()
            log.warning("Job listener disconnected (%s)", type(exc).__name__)

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None
