from types import SimpleNamespace
import httpx
import pytest
from sqlalchemy import select
from app.llm import prompt_cache, prompt_registry as registry
from app.db.models import PromptSnapshot
from app.settings import get_settings


def test_exact_version_survives_transport_outage_and_keeps_reference(pg_db, monkeypatch):
    monkeypatch.setenv('PROMPT_CACHE_ENABLED', 'true')
    monkeypatch.setenv('LANGFUSE_ENABLED', 'true')
    get_settings.cache_clear()
    class Client:
        def get_prompt(self, *_, **__):
            return SimpleNamespace(prompt='Verified original', version=7, is_fallback=False)
    monkeypatch.setattr(registry, 'get_client', lambda: Client())
    ref = {'source':'langfuse', 'name':'talky/en-reply', 'version':7}
    assert registry.read_reference(ref) == 'Verified original'
    def unavailable(*_, **__):
        raise registry.PromptUnavailable('offline') from httpx.ConnectError('offline')
    monkeypatch.setattr(registry, 'remote_prompt', unavailable)
    assert registry.read_reference(ref) == 'Verified original'
    assert registry.resolve_reference('talky/en-reply', version=7) == ref
    with pytest.raises(registry.PromptUnavailable):
        registry.read_reference({**ref, 'version':8})
    with pg_db[1]() as session:
        session.scalars(select(PromptSnapshot)).one().body = 'corrupted'
    with pytest.raises(registry.PromptUnavailable):
        registry.read_reference(ref)


def test_snapshots_are_immutable_and_auth_or_missing_versions_cannot_fallback(pg_db, monkeypatch):
    prompt_cache.save('talky/en-reply', 1, 'original')
    with pytest.raises(ValueError, match='changed'):
        prompt_cache.save('talky/en-reply', 1, 'different')
    assert prompt_cache.load('talky/en-reply', 1) == 'original'
    monkeypatch.setenv('PROMPT_CACHE_ENABLED', 'true')
    get_settings.cache_clear()
    class Unauthorized(Exception):
        status_code = 401
    def unauthorized(*_, **__):
        raise registry.PromptUnavailable('unauthorized') from Unauthorized()
    from datetime import datetime, timedelta, timezone
    with pg_db[1]() as session:
        session.get(PromptSnapshot, ('talky/en-reply', 1)).created_at = datetime.now(timezone.utc)-timedelta(minutes=6)
    monkeypatch.setattr(registry, 'remote_prompt', unauthorized)
    with pytest.raises(registry.PromptUnavailable):
        registry.read_reference({'source':'langfuse','name':'talky/en-reply','version':1})
    assert not prompt_cache.transient(Unauthorized())
    assert not prompt_cache.transient(ValueError())


def test_fresh_exact_snapshot_avoids_remote_call_and_repeated_write(pg_db, monkeypatch):
    monkeypatch.setenv('PROMPT_CACHE_ENABLED', 'true')
    get_settings.cache_clear()
    prompt_cache.save('talky/en-reply', 3, 'verified')
    monkeypatch.setattr(registry, 'remote_prompt', lambda *_a, **_kw: pytest.fail('fresh cache contacted remote'))
    for _ in range(3):
        assert registry.read_reference({'source':'langfuse','name':'talky/en-reply','version':3}) == 'verified'
