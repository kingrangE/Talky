from datetime import timedelta
from threading import Event
from types import SimpleNamespace

from sqlalchemy import func, select

from app import maintenance
from app.db.models import LoginLimit


def test_sweep_is_bounded_and_resumes_without_deleting_referenced_paths(monkeypatch):
    monkeypatch.setattr(maintenance, 'referenced_audio_paths', lambda paths: {'live'} & set(paths))
    deleted = []
    class Store:
        delete = staticmethod(deleted.append)
    iterator = iter(['old1', 'live', 'old2'])
    assert maintenance.sweep_batch(iterator, Store(), limit=2)
    assert deleted == ['old1']
    assert maintenance.sweep_batch(iterator, Store(), limit=2)
    assert deleted == ['old1', 'old2']
    assert not maintenance.sweep_batch(iterator, Store(), limit=2)


def test_only_one_cleanup_owner_and_lock_recovers_on_disconnect(pg_db):
    from sqlalchemy import text
    engine = pg_db[0]
    sql = text('SELECT pg_try_advisory_lock(:key)')
    with engine.connect() as first, engine.connect() as second:
        assert first.scalar(sql, {'key': maintenance.CLEANUP_LOCK})
        assert not second.scalar(sql, {'key': maintenance.CLEANUP_LOCK})
        first.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': maintenance.CLEANUP_LOCK})
        assert second.scalar(sql, {'key': maintenance.CLEANUP_LOCK})
        second.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': maintenance.CLEANUP_LOCK})


def test_maintenance_removes_only_expired_login_limits(pg_db, monkeypatch):
    _, scope = pg_db
    with scope() as session:
        session.add_all([
            LoginLimit(key='expired', attempts=1, expires_at=func.now() - timedelta(minutes=1)),
            LoginLimit(key='active', attempts=1, expires_at=func.now() + timedelta(minutes=1)),
        ])
    stop = Event()
    monkeypatch.setattr(maintenance, 'run_expiry_cleanup', lambda **_: stop.set())
    monkeypatch.setattr(maintenance, 'audio_store', lambda: SimpleNamespace(older_than=lambda _: ()))
    maintenance.run(stop)
    with scope() as session:
        assert session.scalars(select(LoginLimit.key)).all() == ['active']
