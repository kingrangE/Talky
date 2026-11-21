import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.db import postgres, repo
from app.schema_contract import check_schema


@pytest.fixture
def migration_db():
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
        yield engine, migrate
    finally:
        engine.dispose()
        with control.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        control.dispose()


def test_upgrade_preserves_legacy_data_and_can_be_reapplied(migration_db):
    engine, migrate = migration_db
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
        assert connection.scalar(text("SELECT langfuse_name FROM prompt_versions")) is None
        assert connection.scalar(text("SELECT common_prompt_refs FROM conversations")) is None
    with engine.begin() as connection:
        connection.execute(text("UPDATE prompt_versions SET langfuse_name='talky/system', langfuse_version=1, content=''"))
    with pytest.raises(subprocess.CalledProcessError):
        migrate("downgrade", "0004_operational_safety")
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT langfuse_name FROM prompt_versions")) == "talky/system"
        connection.execute(text("UPDATE prompt_versions SET langfuse_name=NULL, langfuse_version=NULL, content='legacy English tutor'"))
    migrate("downgrade", "0003_user_prompts")
    migrate("upgrade", "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT title FROM conversations")) == "legacy conversation"


def test_read_indexes_preserve_existing_data_and_order(migration_db, monkeypatch):
    from app.api import detail

    engine, migrate = migration_db
    previous = "0012_conversation_starter"
    head = "0013_conversation_read_indexes"
    migrate("upgrade", previous)
    monkeypatch.setattr(postgres, "_SessionLocal", sessionmaker(bind=engine, autoflush=False))
    owner, other = uuid.uuid4(), uuid.uuid4()
    first, target, older, other_conversation = [uuid.UUID(int=i) for i in range(1, 5)]
    completed, failed, queued, old_turn, processing = [uuid.UUID(int=i) for i in range(11, 16)]
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO users (id) VALUES (:id)"), [{"id": owner}, {"id": other}])
        connection.execute(text("""INSERT INTO conversations (id, user_id, title, started_at)
            VALUES (:id, :owner, :title, timestamptz '2026-01-01 UTC' + :day * interval '1 day')"""), [
                {"id": cid, "owner": uid, "title": title, "day": day}
                for cid, uid, title, day in [(target, owner, "target", 2), (first, owner, "first", 2),
                                             (older, owner, "older", 1), (other_conversation, other, "other", 3)]
            ])
        connection.execute(text("""INSERT INTO conversation_turns
            (id, conversation_id, user_id, input_digest, input_text, status, queue_deadline, audio_expires_at, created_at)
            VALUES (:id, :cid, :owner, 'digest', 'saved input', :status, now(), now(),
                    timestamptz '2026-01-02 UTC' + :day * interval '1 day')"""), [
                {"id": tid, "cid": cid, "owner": uid, "status": status, "day": day}
                for tid, cid, uid, status, day in [(queued, target, owner, "queued", 2),
                                                   (failed, target, owner, "failed", 2),
                                                   (completed, target, owner, "completed", 2),
                                                   (old_turn, target, owner, "completed", 1),
                                                   (processing, other_conversation, other, "processing", 2)]
            ])

    def inspect_state(revision):
        with engine.connect() as connection:
            check_schema(connection)
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == revision
            indexes = dict(connection.execute(text("""SELECT indexname, indexdef FROM pg_indexes
                WHERE schemaname = current_schema() AND tablename IN ('conversations', 'conversation_turns')""")).all())
            data = [connection.scalars(text(f"SELECT to_jsonb(t) FROM {table} AS t ORDER BY id")).all()
                    for table in ("conversations", "conversation_turns")]
        assert [row["id"] for row in repo.list_conversations(owner)] == list(map(str, (first, target, older)))
        assert [row["id"] for row in repo.list_conversations(owner, offset=1, limit=1)] == [str(target)]
        assert [(turn["id"], turn["status"]) for turn in detail(target, str(owner))["turns"]] == [
            (str(old_turn), "completed"), (str(completed), "completed"), (str(failed), "failed"), (str(queued), "queued"),
        ]
        assert [(turn["id"], turn["status"]) for turn in detail(other_conversation, str(other))["turns"]] == [
            (str(processing), "processing"),
        ]
        return indexes, data

    original_indexes, original_rows = inspect_state(previous)
    with engine.begin() as blocker:
        blocker.execute(text("LOCK TABLE conversation_turns IN ROW EXCLUSIVE MODE"))
        with pytest.raises(subprocess.CalledProcessError) as error:
            migrate("upgrade", "head")
        assert "lock timeout" in error.value.stderr
        assert "ix_conversation_turns_conversation_created_id" in error.value.stderr
    assert inspect_state(previous) == (original_indexes, original_rows)

    migrate("upgrade", "head")
    upgraded_indexes, upgraded_rows = inspect_state(head)
    assert upgraded_rows == original_rows
    assert upgraded_indexes.keys() - original_indexes.keys() == {
        "ix_conversations_user_started_id", "ix_conversation_turns_conversation_created_id",
    }
    assert all(upgraded_indexes[name] == definition for name, definition in original_indexes.items())
    assert upgraded_indexes["ix_conversations_user_started_id"].endswith("(user_id, started_at DESC, id)")
    assert upgraded_indexes["ix_conversation_turns_conversation_created_id"].endswith("(conversation_id, created_at, id)")
    migrate("downgrade", previous)
    assert inspect_state(previous) == (original_indexes, original_rows)
    migrate("upgrade", "head")
    assert inspect_state(head) == (upgraded_indexes, original_rows)
