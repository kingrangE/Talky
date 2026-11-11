"""Read-only fallback for exact managed versions, never mutable labels."""
import hashlib
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
        session.execute(insert(PromptSnapshot).values(name=name, version=version, body=body, digest=digest)
                        .on_conflict_do_nothing(index_elements=['name', 'version']))
        existing = session.get(PromptSnapshot, (name, version))
        if existing.digest != digest or existing.body != body:
            raise ValueError('managed prompt version changed content')


def load(name, version):
    with session_scope() as session:
        cached = session.get(PromptSnapshot, (name, version))
        if cached is None:
            return None
        if hashlib.sha256(cached.body.encode()).hexdigest() != cached.digest:
            raise ValueError('prompt cache integrity failure')
        return cached.body
