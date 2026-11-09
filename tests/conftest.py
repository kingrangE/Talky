import os
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.db import postgres
from app.db.models import Base
from app.settings import get_settings


@pytest.fixture(autouse=True)
def offline_prompt_defaults(monkeypatch):
    # Tests must never contact the user's Cloud project from a local .env file.
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setenv("LANGFUSE_PROMPT_VERSIONS", "{}")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def pg_db(monkeypatch):
    """Opt-in PostgreSQL tests, always in a disposable schema in a test database."""
    dsn = os.environ.get("TALKY_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set TALKY_TEST_POSTGRES_DSN to a dedicated *_test database")
    if not (make_url(dsn).database or "").endswith("_test"):
        pytest.fail("integration tests require a dedicated database ending in _test")
    schema = "talky_test_" + uuid.uuid4().hex
    control = create_engine(dsn)
    with control.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_engine(dsn, connect_args={
        "options": f"-c search_path={schema} -c lock_timeout=5000 -c statement_timeout=30000",
    })
    try:
        Base.metadata.create_all(engine)
        monkeypatch.setattr(postgres, "_engine", engine)
        monkeypatch.setattr(postgres, "_SessionLocal", sessionmaker(bind=engine, autoflush=False))
        yield engine, postgres.session_scope
    finally:
        engine.dispose()
        with control.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        control.dispose()
