import json
from sqlalchemy.engine import make_url
from app.settings import Settings
from app import warmup


def test_generated_database_password_is_url_encoded():
    cfg = Settings(_env_file=None, POSTGRES_PASSWORD='a@:/%#"b')
    assert make_url(cfg.postgres_dsn).password == 'a@:/%#"b'
    assert make_url(cfg.postgres_dsn).query['sslmode'] == 'prefer'


def test_worker_warmup_checks_audio_then_loads_model_without_generation(monkeypatch):
    calls = []
    monkeypatch.setattr(warmup, '_model', lambda: calls.append('stt'))
    monkeypatch.setattr(warmup, '_voice', lambda _: calls.append('tts') or object())
    monkeypatch.setattr(warmup, 'get_settings', lambda: Settings(_env_file=None))
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self): return b'{"done":true}'
    def open_request(request, **kwargs):
        calls.append(json.loads(request.data))
        return Response()
    monkeypatch.setattr(warmup.urllib.request, 'urlopen', open_request)
    warmup.warmup()
    assert calls[:2] == ['stt', 'tts']
    assert calls[2]['prompt'] == '' and calls[2]['stream'] is False


def test_worker_readiness_requires_recent_processing_heartbeat(monkeypatch, tmp_path):
    import os
    import time
    import pytest
    from app import health
    heartbeat = tmp_path/'heartbeat'
    monkeypatch.setattr(health.sys, 'argv', ['health', 'worker'])
    monkeypatch.setattr(health, 'Path', lambda _: heartbeat)
    with pytest.raises(FileNotFoundError):
        health.main()
    heartbeat.touch()
    health.main()
    old = time.time()-1000
    os.utime(heartbeat, (old, old))
    with pytest.raises(RuntimeError, match='expired'):
        health.main()


def test_migration_plan_does_not_contact_aws():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, 'infrastructure/aws/migrate.py', '--stack', 'test',
        '--image', 'example/talky@sha256:'+'a'*64], capture_output=True, text=True, check=True)
    assert 'No AWS requests sent' in result.stdout
