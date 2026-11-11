"""Read-only fallback for exact managed versions, never mutable labels."""
import hashlib
from datetime import datetime, timezone
from sqlalchemy import delete
import logging
import httpx
from sqlalchemy.dialects.postgresql import insert
from app.db.models import PromptSnapshot
from app.db.postgres import session_scope

log = logging.getLogger(__name__)


def transient(error):
    status = getattr(error, 'status_code', None)
    return isinstance(error, (TimeoutError, ConnectionError, httpx.TransportError)) or (
        isinstance(status, int) and (status >= 500 or status == 429))


def save(name, version, body):
    digest = hashlib.sha256(body.encode()).hexdigest()
    with session_scope() as session:
        existing = session.get(PromptSnapshot, (name, version))
        if existing is not None:
            if existing.digest != digest or existing.body != body:
                raise ValueError('managed prompt version changed content')
            existing.created_at = datetime.now(timezone.utc)
            return
        session.execute(insert(PromptSnapshot).values(name=name, version=version, body=body, digest=digest)
                        .on_conflict_do_nothing(index_elements=['name', 'version']))
        existing = session.get(PromptSnapshot, (name, version))
        if existing.digest != digest or existing.body != body:
            raise ValueError('managed prompt version changed content')


def load(name, version, *, max_age=None):
    with session_scope() as session:
        cached = session.get(PromptSnapshot, (name, version))
        if cached is None:
            return None
        if max_age is not None and (datetime.now(timezone.utc) - cached.created_at).total_seconds() > max_age:
            return None
        if hashlib.sha256(cached.body.encode()).hexdigest() != cached.digest:
            raise ValueError('prompt cache integrity failure')
        return cached.body


def invalidate(name, version):
    with session_scope() as session:
        session.execute(delete(PromptSnapshot).where(PromptSnapshot.name == name, PromptSnapshot.version == version))
