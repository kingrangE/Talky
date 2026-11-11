from __future__ import annotations

import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, or_, select

from app.db.models import (
    MockExamCalibrationSample,
    MockExamDailyAggregate,
    MockExamReport,
    MockExamResponse,
    MockExamSession,
    PendingAudioDeletion,
)
from app.db.postgres import session_scope
from app.mock_exam import CONSENT_VERSION, SCORING_VERSION
from app.settings import get_settings

log = logging.getLogger(__name__)


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def choose_scoring_profile() -> str:
    configured = get_settings().MOCK_EXAM_SCORING_PROFILE
    if configured in {"basic", "advanced"}:
        return configured
    try:
        import torch

        return "advanced" if torch.cuda.is_available() else "basic"
    except ImportError:
        return "basic"


def _active_sessions(now):
    cutoff = now - timedelta(minutes=get_settings().MOCK_EXAM_RECOVERY_TTL_MINUTES)
    queued_response = select(MockExamResponse.id).where(
        MockExamResponse.session_id == MockExamSession.id,
        MockExamResponse.status.in_(["queued", "processing"]),
    ).exists()
    queued_report = select(MockExamReport.id).where(
        MockExamReport.session_id == MockExamSession.id,
        MockExamReport.status.in_(["queued", "scoring"]),
    ).exists()
    return select(func.count(MockExamSession.id)).where(
        MockExamSession.status.in_(["active", "scoring"]),
        MockExamSession.expires_at > now,
        or_(MockExamSession.updated_at >= cutoff, queued_response, queued_report),
    )


def quota_status(visitor_hash: str) -> dict[str, int | bool]:
    cfg = get_settings()
    now = _now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with session_scope() as s:
        daily = int(s.scalar(
            select(func.count(MockExamSession.id)).where(
                MockExamSession.visitor_hash == visitor_hash,
                MockExamSession.started_at >= day_start,
            )
        ) or 0)
        active = int(s.scalar(_active_sessions(now)) or 0)
    return {
        "daily_used": daily,
        "daily_limit": cfg.MOCK_EXAM_DAILY_LIMIT,
        "active": active,
        "global_limit": cfg.MOCK_EXAM_GLOBAL_CONCURRENCY,
        "allowed": daily < cfg.MOCK_EXAM_DAILY_LIMIT and active < cfg.MOCK_EXAM_GLOBAL_CONCURRENCY,
    }


def create_session(
    *, visitor_hash: str, exam_set_id: str, exam_set_version: str
) -> dict[str, Any]:
    cfg = get_settings()
    now = _now()
    expires = now + timedelta(hours=cfg.MOCK_EXAM_RESULT_TTL_HOURS)
    profile = choose_scoring_profile()
    with session_scope() as s:
        # Serialize the public gate so two simultaneous starts cannot both pass the limit.
        s.execute(select(func.pg_advisory_xact_lock(74201931)))
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        daily = int(s.scalar(select(func.count(MockExamSession.id)).where(
            MockExamSession.visitor_hash == visitor_hash,
            MockExamSession.started_at >= day_start,
        )) or 0)
        active = int(s.scalar(_active_sessions(now)) or 0)
        if daily >= cfg.MOCK_EXAM_DAILY_LIMIT or active >= cfg.MOCK_EXAM_GLOBAL_CONCURRENCY:
            raise PermissionError("mock exam quota exceeded")
        row = MockExamSession(
            visitor_hash=visitor_hash,
            exam_set_id=exam_set_id,
            exam_set_version=exam_set_version,
            scoring_profile=profile,
            consent_version=CONSENT_VERSION,
            expires_at=expires,
        )
        s.add(row)
        s.flush()
        return {"id": str(row.id), "scoring_profile": profile, "expires_at": expires}


def get_session(session_id: str) -> dict[str, Any] | None:
    with session_scope() as s:
        row = s.get(MockExamSession, _uuid(session_id))
        if row is None or row.expires_at < _now():
            return None
        return {
            "id": str(row.id),
            "exam_set_id": row.exam_set_id,
            "exam_set_version": row.exam_set_version,
            "status": row.status,
            "current_question": int(row.current_question),
            "retry_question": row.retry_question,
            "scoring_profile": row.scoring_profile,
            "started_at": row.started_at,
            "updated_at": row.updated_at,
            "completed_at": row.completed_at,
            "expires_at": row.expires_at,
        }


def create_response(
    *, session_id: str, question_number: int, encrypted_audio_path: str,
    client_metrics: dict[str, Any],
) -> str:
    expires = _now() + timedelta(hours=get_settings().MOCK_EXAM_AUDIO_TTL_HOURS)
    with session_scope() as s:
        session = s.scalars(select(MockExamSession).where(
            MockExamSession.id == _uuid(session_id)
        ).with_for_update()).first()
        if (session is None or session.status != "active" or session.expires_at < _now()
                or session.current_question != question_number):
            raise ValueError("question is not the active checkpoint")
        session.updated_at = _now()
        existing = s.scalars(
            select(MockExamResponse).where(
                MockExamResponse.session_id == _uuid(session_id),
                MockExamResponse.question_number == question_number,
            )
        ).first()
        if existing is not None:
            if existing.status != "technical_error" or existing.attempts >= get_settings().MOCK_EXAM_MAX_ATTEMPTS:
                raise ValueError("response already submitted")
            if existing.encrypted_audio_path:
                s.merge(PendingAudioDeletion(path=existing.encrypted_audio_path))
            existing.status = "queued"
            existing.encrypted_audio_path = encrypted_audio_path
            existing.transcript = None
            existing.audio_metrics = {"browser": client_metrics}
            existing.language_evaluation = None
            existing.task_evaluation = None
            existing.error_code = None
            existing.updated_at = _now()
            return str(existing.id)
        row = MockExamResponse(
            session_id=_uuid(session_id),
            question_number=question_number,
            encrypted_audio_path=encrypted_audio_path,
            audio_metrics={"browser": client_metrics},
            expires_at=expires,
        )
        s.add(row)
        s.flush()
        return str(row.id)


def list_responses(session_id: str) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.scalars(
            select(MockExamResponse)
            .where(MockExamResponse.session_id == _uuid(session_id))
            .order_by(MockExamResponse.question_number)
        ).all()
        return [
            {
                "id": str(row.id),
                "question_number": int(row.question_number),
                "status": row.status,
                "attempts": row.attempts,
                "encrypted_audio_path": row.encrypted_audio_path,
                "transcript": row.transcript or "",
                "audio_metrics": row.audio_metrics or {},
                "language_evaluation": row.language_evaluation or {},
                "task_evaluation": row.task_evaluation or {},
                "error_code": row.error_code,
            }
            for row in rows
        ]


def save_report(
    session_id: str, *, status: str, payload: dict[str, Any], final: bool = True,
    evaluations: list[dict[str, Any]] | None = None,
    expected_attempt: int | None = None,
) -> None:
    sid = _uuid(session_id)
    expires = _now() + timedelta(hours=get_settings().MOCK_EXAM_RESULT_TTL_HOURS)
    with session_scope() as s:
        session = s.scalars(select(MockExamSession).where(MockExamSession.id == sid).with_for_update()).first()
        if session is None:
            raise ValueError("mock exam session not found")
        row = s.scalars(select(MockExamReport).where(MockExamReport.session_id == sid)).first()
        if expected_attempt is not None and (
            row is None or row.attempts != expected_attempt or row.status != "scoring"
        ):
            raise ValueError("stale scoring attempt")
        if not final and (session.status != "scoring" or (row and row.status not in {"queued", "scoring"})):
            return
        if not final and row is not None:
            # Publishing an interim result must not erase the durable worker state.
            row.payload = payload
            row.score_low = payload.get("score_low")
            row.score_high = payload.get("score_high")
            row.expected_level = payload.get("expected_level")
            row.confidence = payload.get("confidence")
            return
        values = {
            "status": status,
            "scoring_version": payload.get("scoring_version", SCORING_VERSION),
            "score_low": payload.get("score_low"),
            "score_high": payload.get("score_high"),
            "expected_level": payload.get("expected_level"),
            "confidence": payload.get("confidence"),
            "scoring_profile": session.scoring_profile,
            "payload": payload,
            "updated_at": _now(),
            "expires_at": min(expires, session.expires_at),
        }
        if row is None:
            row = MockExamReport(session_id=sid, created_at=_now(), **values)
            s.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        if evaluations:
            for evaluation in evaluations:
                response = s.get(MockExamResponse, _uuid(evaluation["id"]))
                if response is None or response.session_id != sid:
                    raise ValueError("response does not belong to exam")
                response.language_evaluation = evaluation["language_evaluation"]
                response.task_evaluation = evaluation["task_evaluation"]
                response.updated_at = _now()
        if final and status in {"completed", "partial"}:
            session.status = "completed"
            session.updated_at = _now()
            session.completed_at = _now()


def get_report(session_id: str) -> dict[str, Any] | None:
    with session_scope() as s:
        row = s.scalars(
            select(MockExamReport).where(MockExamReport.session_id == _uuid(session_id))
        ).first()
        if row is None or row.expires_at < _now():
            return None
        return {
            "status": "partial" if row.status in {"queued", "scoring"} and (row.payload or {}).get("partial_reason") else row.status,
            "scoring_version": row.scoring_version,
            "score_low": row.score_low,
            "score_high": row.score_high,
            "expected_level": row.expected_level,
            "confidence": row.confidence,
            "scoring_profile": row.scoring_profile,
            "payload": row.payload or {},
            "updated_at": row.updated_at,
            "expires_at": row.expires_at,
        }


def save_calibration_sample(
    *, official_score: int, official_exam_month: str | None, report: dict[str, Any]
) -> None:
    if official_score % 10 or not 0 <= official_score <= 200:
        raise ValueError("official score must be 0..200 in increments of 10")
    with session_scope() as s:
        s.add(MockExamCalibrationSample(
            official_score=official_score,
            official_exam_month=official_exam_month,
            predicted_low=int(report["score_low"]),
            predicted_high=int(report["score_high"]),
            scoring_version=str(report["scoring_version"]),
            scoring_profile=str(report["scoring_profile"]),
        ))


def claim_job() -> dict[str, Any] | None:
    """Claim a committed job without blocking other workers."""
    now = _now()
    with session_scope() as s:
        for model, queued, processing, kind in (
            (MockExamResponse, "queued", "processing", "response"),
            (MockExamReport, "queued", "scoring", "report"),
        ):
            row = s.scalars(select(model).join(MockExamSession).where(
                model.status == queued,
                or_(model.lease_until.is_(None), model.lease_until <= now),
                model.expires_at > now,
                MockExamSession.expires_at > now,
                MockExamSession.status.in_(["active", "scoring"]),
            ).order_by(model.created_at, model.id).with_for_update(of=model, skip_locked=True).limit(1)).first()
            if row is None:
                continue
            row.status = processing
            row.attempts += 1
            row.lease_until = now + timedelta(seconds=get_settings().MOCK_EXAM_JOB_TIMEOUT_SECONDS + 30)
            row.updated_at = now
            return {
                "id": str(row.id), "session_id": str(row.session_id), "kind": kind,
                "attempt": row.attempts, "created_at": row.created_at.isoformat(),
                **({"question_number": row.question_number,
                    "encrypted_audio_path": row.encrypted_audio_path,
                    "client_metrics": (row.audio_metrics or {}).get("browser", {})}
                   if kind == "response" else {}),
            }
    return None


def _retry_or_fail(s, row, kind: str, error_code: str) -> None:
    session = s.get(MockExamSession, row.session_id)
    if session is None:
        return
    row.updated_at = _now()
    row.lease_until = None
    session.updated_at = _now()
    from app.retry import retry_delay, error_name
    if kind == "response":
        row.error_code = error_name(error_code)
    delay = retry_delay(error_code, row.attempts)
    if (delay is not None and row.attempts < get_settings().MOCK_EXAM_MAX_ATTEMPTS
            and _now() + delay < row.expires_at):
        row.lease_until = _now() + delay
        row.status = "queued"
    else:
        row.status = "failed"
        session.status = "failed"


def fail_job(job: dict[str, Any], error_code: str) -> None:
    model = MockExamResponse if job["kind"] == "response" else MockExamReport
    with session_scope() as s:
        s.scalars(select(MockExamSession).where(
            MockExamSession.id == _uuid(job["session_id"])
        ).with_for_update()).first()
        row = s.get(model, _uuid(job["id"]))
        if (row is not None and row.status in {"processing", "scoring"}
                and row.attempts == job["attempt"]):
            _retry_or_fail(s, row, job["kind"], error_code)


def recover_jobs() -> int:
    """Recover expired claims only; a bounded supervisor kills work before its lease expires."""
    recovered = 0
    with session_scope() as s:
        for model, processing, kind in (
            (MockExamResponse, "processing", "response"),
            (MockExamReport, "scoring", "report"),
        ):
            expired = select(model.id).where(model.session_id == MockExamSession.id,
                model.status == processing, model.lease_until <= _now()).exists()
            sessions = s.scalars(select(MockExamSession).where(expired)
                .with_for_update(skip_locked=True).limit(32)).all()
            for session in sessions:
                for row in s.scalars(select(model).where(model.session_id == session.id,
                        model.status == processing, model.lease_until <= _now())
                        .with_for_update(skip_locked=True)).all():
                    _retry_or_fail(s, row, kind, "worker_interrupted")
                    recovered += 1
    return recovered


def complete_response(
    response_id: str, *, status: str, transcript: str, audio_metrics: dict[str, Any],
    error_code: str | None = None,
    expected_attempt: int | None = None,
) -> None:
    with session_scope() as s:
        row = s.get(MockExamResponse, _uuid(response_id))
        if row is None:
            raise ValueError("response not found")
        session = s.scalars(select(MockExamSession).where(
            MockExamSession.id == row.session_id
        ).with_for_update()).first()
        s.refresh(row)
        if session is None or row.status != "processing" or session.expires_at < _now():
            raise ValueError("response is no longer processing")
        if expected_attempt is not None and row.attempts != expected_attempt:
            raise ValueError("stale response attempt")
        row.status = status
        row.transcript = transcript
        row.audio_metrics = audio_metrics
        row.error_code = error_code
        row.updated_at = session.updated_at = _now()
        if status == "technical_error":
            session.retry_question = row.question_number
            if row.attempts >= get_settings().MOCK_EXAM_MAX_ATTEMPTS:
                row.status = session.status = "failed"
            return
        session.retry_question = None
        if row.question_number < 11:
            session.current_question = row.question_number + 1
        else:
            session.status = "scoring"
            s.add(MockExamReport(
                session_id=session.id, status="queued", scoring_version=SCORING_VERSION,
                scoring_profile=session.scoring_profile, payload={}, expires_at=session.expires_at,
            ))


def requeue_report(session_id: str) -> None:
    with session_scope() as s:
        s.execute(select(func.pg_advisory_xact_lock(74201931)))
        if int(s.scalar(_active_sessions(_now())) or 0) >= get_settings().MOCK_EXAM_GLOBAL_CONCURRENCY:
            raise ValueError("다른 시험이 진행 중입니다. 잠시 후 다시 요청하세요.")
        session = s.scalars(select(MockExamSession).where(
            MockExamSession.id == _uuid(session_id)
        ).with_for_update()).first()
        report = s.scalars(select(MockExamReport).where(
            MockExamReport.session_id == _uuid(session_id)
        )).first()
        if (session is None or session.expires_at < _now() or report is None
                or report.status not in {"partial", "failed"}
                or report.attempts >= get_settings().MOCK_EXAM_MAX_ATTEMPTS):
            raise ValueError("평가를 다시 요청할 수 없습니다.")
        report.status = "queued"
        report.payload = {}
        report.updated_at = session.updated_at = _now()
        session.status = "scoring"


def queue_audio_deletion(path: str) -> None:
    with session_scope() as s:
        s.merge(PendingAudioDeletion(path=path))


def cleanup_expired(audio_delete) -> int:
    """Commit metadata removal and a deletion outbox before touching audio files."""
    now = _now()
    removed = 0
    with session_scope() as s:
        if s.get_bind().dialect.name == "postgresql":
            s.execute(select(func.pg_advisory_xact_lock(74201934)))
        rows = s.scalars(
            select(MockExamSession).where(MockExamSession.expires_at < now).with_for_update()
        ).all()
        for session in rows:
            day = session.started_at.date().isoformat()
            agg = s.scalars(select(MockExamDailyAggregate).where(
                MockExamDailyAggregate.day == day,
                MockExamDailyAggregate.scoring_profile == session.scoring_profile,
            )).first()
            if agg is None:
                agg = MockExamDailyAggregate(
                    day=day,
                    scoring_profile=session.scoring_profile,
                    started_count=0,
                    completed_count=0,
                    total_processing_ms=0,
                )
                s.add(agg)
                s.flush()
            agg.started_count += 1
            if session.status == "completed":
                agg.completed_count += 1
            agg.updated_at = now
            for response in session.responses:
                if response.encrypted_audio_path:
                    s.merge(PendingAudioDeletion(path=response.encrypted_audio_path))
            s.delete(session)
            removed += 1
        s.flush()
        for response in s.scalars(select(MockExamResponse).where(MockExamResponse.expires_at < now)).all():
            if response.encrypted_audio_path:
                s.merge(PendingAudioDeletion(path=response.encrypted_audio_path))
            s.delete(response)
        s.execute(delete(MockExamReport).where(MockExamReport.expires_at < now))
    with session_scope() as s:
        pending = list(s.scalars(select(PendingAudioDeletion.path)).all())
    for path in pending:
        try:
            audio_delete(path)
        except OSError:
            log.warning("audio deletion deferred")
            continue
        with session_scope() as s:
            s.execute(delete(PendingAudioDeletion).where(PendingAudioDeletion.path == path))
    return removed


def referenced_audio_paths() -> set[str]:
    from app.db.models import ConversationTurn
    with session_scope() as s:
        paths = set(s.scalars(select(MockExamResponse.encrypted_audio_path).where(
            MockExamResponse.encrypted_audio_path.is_not(None)
        )).all())
        for row in s.execute(select(ConversationTurn.input_audio_path, ConversationTurn.output_audio_path).where(
                or_(ConversationTurn.input_audio_path.is_not(None), ConversationTurn.output_audio_path.is_not(None)))):
            paths.update(path for path in row if path)
        return paths
