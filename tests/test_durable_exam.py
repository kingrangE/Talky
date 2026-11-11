import io
import base64
import multiprocessing
import threading
import time
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, func, select

from app.audio.validation import decode_audio_upload, validate_audio
from app.db.models import (
    MockExamDailyAggregate, MockExamReport, MockExamResponse, MockExamSession,
    PendingAudioDeletion,
)
from app.mock_exam import repository as repo, service, worker
from app.mock_exam.privacy import EncryptedAudioStore
from app.runtime import ModelBusy, inference_slot


@pytest.fixture
def exam(pg_db, tmp_path, monkeypatch):
    store = EncryptedAudioStore(tmp_path / "audio")
    monkeypatch.setattr(service, "audio_store", lambda: store)
    created = repo.create_session(
        visitor_hash=uuid.uuid4().hex, exam_set_id="workplace-basics-001", exam_set_version="1.0.0"
    )
    return created["id"], store


def _expire_claims(pg_db):
    with pg_db[1]() as session:
        for model in (MockExamResponse, MockExamReport):
            for row in session.scalars(select(model).where(model.status.in_(['processing', 'scoring']))):
                row.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)


def _submit(sid, number=1, audio=b"a" * 128):
    return service.submit_response(session_id=sid, question_number=number,
                                   audio_bytes=audio, client_metrics={"signal_detected": True})


def test_concurrent_duplicate_upload_preserves_accepted_audio(exam, pg_db, monkeypatch):
    sid, store = exam
    barrier = threading.Barrier(2)

    def validate(*_args, **_kwargs):
        barrier.wait(timeout=5)

    monkeypatch.setattr(service, "validate_audio", validate)

    def submit(audio):
        try:
            return _submit(sid, audio=audio), audio
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(submit, [b"a" * 128, b"b" * 128]))
    accepted = [outcome for outcome in outcomes if outcome]
    assert len(accepted) == 1
    rows = repo.list_responses(sid)
    assert len(rows) == 1
    assert rows[0]["id"] == accepted[0][0]
    assert store.load(rows[0]["encrypted_audio_path"]) == accepted[0][1]
    assert len(list(store.root.glob("*/*.fernet"))) == 1


def test_committed_job_recovers_after_worker_restart_and_stops_retrying(exam, pg_db, monkeypatch):
    sid, _ = exam
    monkeypatch.setattr(service, "validate_audio", lambda *_args, **_kwargs: None)
    rid = _submit(sid)
    for attempt in range(3):
        job = repo.claim_job()
        assert job["id"] == rid
        assert repo.claim_job() is None
        assert repo.recover_jobs() == 0
        _expire_claims(pg_db)
        assert repo.recover_jobs() == 1
    assert repo.claim_job() is None
    assert repo.get_session(sid)["status"] == "failed"


def test_old_worker_cannot_finish_or_fail_a_new_attempt(exam, pg_db, monkeypatch):
    sid, _ = exam
    monkeypatch.setattr(service, "validate_audio", lambda *_args, **_kwargs: None)
    rid = _submit(sid)
    stale = repo.claim_job()
    _expire_claims(pg_db)
    repo.recover_jobs()
    current = repo.claim_job()
    with pytest.raises(ValueError, match="stale"):
        repo.complete_response(rid, status="scored", transcript="stale", audio_metrics={},
                               expected_attempt=stale["attempt"])
    repo.fail_job(stale, "late error")
    assert repo.list_responses(sid)[0]["status"] == "processing"
    repo.complete_response(rid, status="scored", transcript="current", audio_metrics={},
                           expected_attempt=current["attempt"])
    assert repo.list_responses(sid)[0]["transcript"] == "current"


def test_old_session_with_pending_work_still_counts_toward_global_limit(exam, pg_db, monkeypatch):
    sid, _ = exam
    _, scope = pg_db
    monkeypatch.setattr(service, "validate_audio", lambda *_args, **_kwargs: None)
    _submit(sid)
    with scope() as session:
        session.get(MockExamSession, uuid.UUID(sid)).updated_at = datetime.now(timezone.utc) - timedelta(minutes=20)
    assert repo.quota_status("another visitor")["active"] == 1
    with pytest.raises(PermissionError):
        repo.create_session(visitor_hash="another visitor", exam_set_id="fixture", exam_set_version="1")


def test_queued_exam_gets_the_next_slot_instead_of_starving_behind_chat(exam, monkeypatch):
    sid, _ = exam
    monkeypatch.setattr(service, "validate_audio", lambda *_args, **_kwargs: None)
    _submit(sid)
    with pytest.raises(ModelBusy):
        with inference_slot():
            pytest.fail("chat overtook queued exam work")
    with inference_slot(for_exam=True):
        assert repo.claim_job()["session_id"] == sid


def test_last_response_and_scoring_enqueue_commit_together(exam, pg_db, monkeypatch):
    sid, _ = exam
    _, scope = pg_db
    monkeypatch.setattr(service, "validate_audio", lambda *_args, **_kwargs: None)
    with scope() as session:
        session.get(MockExamSession, uuid.UUID(sid)).current_question = 11
    rid = _submit(sid, number=11)
    repo.claim_job()

    def fail_enqueue(*_args):
        raise RuntimeError("injected enqueue failure")

    event.listen(MockExamReport, "before_insert", fail_enqueue)
    try:
        with pytest.raises(RuntimeError):
            repo.complete_response(rid, status="scored", transcript="hello", audio_metrics={})
    finally:
        event.remove(MockExamReport, "before_insert", fail_enqueue)
    assert repo.list_responses(sid)[0]["status"] == "processing"
    assert repo.get_session(sid)["status"] == "active"
    repo.complete_response(rid, status="scored", transcript="hello", audio_metrics={})
    assert repo.get_session(sid)["status"] == "scoring"
    assert repo.claim_job()["kind"] == "report"


def test_cleanup_commits_metadata_then_retries_failed_file_deletions(pg_db, tmp_path):
    _, scope = pg_db
    store = EncryptedAudioStore(tmp_path / "audio")
    past = datetime.now(timezone.utc) - timedelta(days=4)
    paths = []
    with scope() as session:
        for _ in range(2):
            sid = uuid.uuid4()
            session.add(MockExamSession(
                id=sid, visitor_hash=uuid.uuid4().hex, exam_set_id="fixture", exam_set_version="1",
                consent_version="fixture", scoring_profile="basic", started_at=past,
                expires_at=past + timedelta(hours=72),
            ))
            session.flush()
            path = store.save(str(sid), 1, b"test audio")
            paths.append(path)
            session.add(MockExamResponse(session_id=sid, question_number=1,
                                         encrypted_audio_path=path, expires_at=past + timedelta(hours=72)))

    def fail_delete(_path):
        raise OSError("injected disk failure")

    assert repo.cleanup_expired(fail_delete) == 2
    with scope() as session:
        assert session.scalar(select(func.count(MockExamSession.id))) == 0
        assert session.scalar(select(func.count(PendingAudioDeletion.path))) == 2
        aggregate = session.scalars(select(MockExamDailyAggregate)).one()
        assert aggregate.started_count == 2
    assert all((store.root / path).exists() for path in paths)
    assert repo.cleanup_expired(store.delete) == 0
    assert all(not (store.root / path).exists() for path in paths)
    with scope() as session:
        assert session.scalar(select(func.count(PendingAudioDeletion.path))) == 0


def test_database_cleanup_failure_does_not_delete_audio(exam, pg_db):
    sid, store = exam
    _, scope = pg_db
    path = store.save(sid, 1, b"must be preserved")
    with scope() as session:
        row = session.get(MockExamSession, uuid.UUID(sid))
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.add(MockExamResponse(session_id=row.id, question_number=1,
                                     encrypted_audio_path=path, expires_at=row.expires_at))

    def fail_aggregate(*_args):
        raise RuntimeError("injected transaction failure")

    event.listen(MockExamDailyAggregate, "before_insert", fail_aggregate)
    try:
        with pytest.raises(RuntimeError):
            repo.cleanup_expired(store.delete)
    finally:
        event.remove(MockExamDailyAggregate, "before_insert", fail_aggregate)
    assert store.load(path) == b"must be preserved"
    with scope() as session:
        assert session.get(MockExamSession, uuid.UUID(sid)) is not None


def test_interim_report_does_not_cancel_job_or_overwrite_final_result(exam, pg_db):
    sid, _ = exam
    _, scope = pg_db
    with scope() as session:
        row = session.get(MockExamSession, uuid.UUID(sid))
        row.status = "scoring"
        session.add(MockExamReport(session_id=row.id, status="queued", payload={},
                                   scoring_version="beta-1", scoring_profile="basic", expires_at=row.expires_at))
    job = repo.claim_job()
    repo.save_report(sid, status="partial", payload={"partial_reason": "waiting", "score_low": 50}, final=False)
    assert repo.get_report(sid)["status"] == "partial"
    assert repo.get_report(sid)["score_low"] == 50
    _expire_claims(pg_db)
    assert repo.recover_jobs() == 1
    assert repo.claim_job()["id"] == job["id"]
    repo.save_report(sid, status="completed", payload={"score_low": 100, "score_high": 140})
    repo.save_report(sid, status="partial", payload={"partial_reason": "late timeout"}, final=False)
    assert repo.get_report(sid)["status"] == "completed"
    assert repo.get_report(sid)["score_low"] == 100


def _hanging_job(channel):
    channel.send("started")
    time.sleep(30)


def _test_db_worker(dsn, schema, audio_root, channel, hang):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import postgres

    postgres._engine = create_engine(dsn, connect_args={"options": f"-c search_path={schema}"})
    postgres._SessionLocal = sessionmaker(bind=postgres._engine, autoflush=False)
    service.audio_store = lambda: EncryptedAudioStore(audio_root)

    def recognize(_audio):
        if hang:
            time.sleep(30)
        return {"text": "recognized after restart", "word_count": 3}

    service.transcribe_detailed = recognize
    worker._run_worker(channel)


def test_real_worker_process_recovers_killed_stt_and_advances_checkpoint(exam, pg_db, monkeypatch):
    from sqlalchemy import text

    sid, store = exam
    engine, _ = pg_db
    monkeypatch.setattr(service, "validate_audio", lambda *_args, **_kwargs: None)
    _submit(sid)
    with engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    context = multiprocessing.get_context("spawn")
    for hang in (True, False):
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=_test_db_worker, args=(
            engine.url.render_as_string(hide_password=False), schema, store.root, sender, hang,
        ))
        process.start()
        sender.close()
        try:
            if hang:
                assert worker._monitor(process, receiver, 0.1, threading.Event())
                assert repo.list_responses(sid)[0]["status"] == "processing"
                _expire_claims(pg_db)
            else:
                assert receiver.poll(15) and receiver.recv() == "started"
                assert receiver.poll(15) and receiver.recv() == "idle"
                assert repo.list_responses(sid)[0]["transcript"] == "recognized after restart"
                assert repo.get_session(sid)["current_question"] == 2
        finally:
            worker._stop_process(process)
            receiver.close()
            process.close()


def test_supervisor_kills_stuck_work_instead_of_leaving_a_thread_running():
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_hanging_job, args=(sender,))
    process.start()
    sender.close()
    try:
        assert worker._monitor(process, receiver, 0.1, threading.Event()) is True
        assert not process.is_alive()
    finally:
        worker._stop_process(process)
        receiver.close()
        process.close()


def _wav(seconds):
    output = io.BytesIO()
    with wave.open(output, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(16000)
        recording.writeframes(b"\0\0" * int(seconds * 16000))
    return output.getvalue()


def test_audio_duration_is_checked_from_decoded_audio():
    assert validate_audio(_wav(1), max_seconds=2) == pytest.approx(1)
    with pytest.raises(ValueError, match="녹음 시간"):
        validate_audio(_wav(3), max_seconds=2)
    with pytest.raises(ValueError):
        validate_audio(b"not audio" * 100)


def test_oversized_encoded_audio_is_rejected_before_allocating_decoded_bytes(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr("app.audio.validation.get_settings", lambda: SimpleNamespace(MAX_AUDIO_BYTES=128))
    assert decode_audio_upload(base64.b64encode(b"a" * 128).decode()) == b"a" * 128
    with pytest.raises(ValueError):
        decode_audio_upload(base64.b64encode(b"a" * 129).decode())

    def must_not_decode(*_args, **_kwargs):
        pytest.fail("oversized input reached the decoder")

    monkeypatch.setattr("app.audio.validation.base64.b64decode", must_not_decode)
    with pytest.raises(ValueError):
        decode_audio_upload("a" * 1000)


def test_new_uploads_have_distinct_paths_and_share_one_persistent_key(tmp_path):
    first = EncryptedAudioStore(tmp_path / "audio")
    second = EncryptedAudioStore(tmp_path / "audio")
    path_a = first.save("session", 1, b"first")
    path_b = second.save("session", 1, b"second")
    assert path_a != path_b
    assert second.load(path_a) == b"first"
    assert first.load(path_b) == b"second"
