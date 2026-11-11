import os
import uuid
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from app.db import runtime_role


def test_runtime_role_can_write_but_cannot_change_schema(monkeypatch):
    dsn = os.environ.get('TALKY_TEST_POSTGRES_DSN')
    if not dsn:
        pytest.skip('requires dedicated *_test PostgreSQL')
    url = make_url(dsn)
    assert url.database.endswith('_test')
    control = create_engine(url, isolation_level='AUTOCOMMIT')
    name = 'talky_role_' + uuid.uuid4().hex + '_test'
    role = 'talky_runtime'
    with control.connect() as connection:
        assert not connection.scalar(text('SELECT 1 FROM pg_roles WHERE rolname=:name'), {'name':role}), 'runtime role exists; use a fresh dedicated test container'
        connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
    owner = create_engine(url.set(database=name))
    runtime = None
    try:
        with owner.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE public.alembic_version (version_num text)')
            connection.exec_driver_sql('CREATE TABLE public.probe (id int primary key, value text)')
        monkeypatch.setattr(runtime_role, 'get_engine', lambda: owner)
        monkeypatch.setenv('RUNTIME_DB_USER', role)
        monkeypatch.setenv('RUNTIME_DB_PASSWORD', 'local@runtime:%test')
        runtime_role.main()
        runtime_role.main()
        runtime = create_engine(url.set(database=name, username=role, password='local@runtime:%test'))
        with runtime.begin() as connection:
            connection.exec_driver_sql("INSERT INTO public.probe VALUES (1, 'ok')")
            assert connection.scalar(text('SELECT value FROM public.probe')) == 'ok'
        for sql in ['CREATE TABLE public.forbidden (id int)', 'ALTER TABLE public.probe ADD COLUMN forbidden int',
                    'SELECT * FROM public.alembic_version']:
            with pytest.raises(DBAPIError):
                with runtime.begin() as connection:
                    connection.exec_driver_sql(sql)
    finally:
        if runtime:
            runtime.dispose()
        with owner.begin() as connection:
            connection.exec_driver_sql('DROP OWNED BY talky_runtime')
        owner.dispose()
        with control.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{name}"')
            connection.exec_driver_sql('DROP ROLE IF EXISTS talky_runtime')
        control.dispose()
