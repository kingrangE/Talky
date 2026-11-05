import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def test_upgrade_preserves_legacy_data_and_can_be_reapplied():
    dsn = os.environ.get("TALKY_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set TALKY_TEST_POSTGRES_DSN to a dedicated *_test database")
    url = make_url(dsn)
    if not (url.database or "").endswith("_test"):
        pytest.fail("migration tests require a dedicated *_test database")
    schema = "talky_migration_test_" + uuid.uuid4().hex
    control = create_engine(dsn)
    engine = create_engine(dsn, connect_args={"options": f"-c search_path={schema}"})
    environment = {
        **os.environ, "PGOPTIONS": f"-c search_path={schema}",
        "POSTGRES_HOST": url.host, "POSTGRES_PORT": str(url.port or 5432),
        "POSTGRES_DB": url.database, "POSTGRES_USER": url.username,
        "POSTGRES_PASSWORD": url.password or "",
    }

    def migrate(command, revision):
        subprocess.run([sys.executable, "-m", "alembic", command, revision],
                       cwd=Path(__file__).resolve().parents[1], env=environment,
                       check=True, capture_output=True, text=True, timeout=30)

    with control.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    try:
        migrate("upgrade", "0003_user_prompts")
        uid, pid, cid = (uuid.uuid4() for _ in range(3))
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO users (id, display_name) VALUES (:id, 'legacy')"), {"id": uid})
            connection.execute(text("""INSERT INTO prompt_versions (id, user_id, version, content, active)
                VALUES (:id, :uid, 1, 'legacy English tutor', true)"""), {"id": pid, "uid": uid})
            connection.execute(text("""INSERT INTO conversations (id, user_id, title, prompt_version_id)
                VALUES (:id, :uid, 'legacy conversation', :pid)"""), {"id": cid, "uid": uid, "pid": pid})
            connection.execute(text("""INSERT INTO ratings (conversation_id, prompt_version_id, stars)
                VALUES (:cid, :pid, 5)"""), {"cid": cid, "pid": pid})
        migrate("upgrade", "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT login_name, password_hash, failed_logins FROM users")).one() == (None, None, 0)
            assert connection.execute(text("SELECT stars, revision FROM ratings")).one() == (5, 1)
            assert connection.scalar(text("SELECT content FROM prompt_versions")) == "legacy English tutor"
            assert connection.scalar(text("SELECT title FROM conversations")) == "legacy conversation"
        migrate("downgrade", "0003_user_prompts")
        migrate("upgrade", "head")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT title FROM conversations")) == "legacy conversation"
    finally:
        engine.dispose()
        with control.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        control.dispose()
