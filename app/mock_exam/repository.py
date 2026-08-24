from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select, update

from app.db.models import (
    MockExamCalibrationSample,
    MockExamDailyAggregate,
    MockExamReport,
    MockExamResponse,
    MockExamSession,
)
from app.db.postgres import session_scope
from app.mock_exam import CONSENT_VERSION, SCORING_VERSION
from app.settings import get_settings


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


def quota_status(visitor_hash: str) -> dict[str, int | bool]:
    cfg = get_settings()
    now = _now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    recovery_cutoff = now - timedelta(minutes=cfg.MOCK_EXAM_RECOVERY_TTL_MINUTES)
    with session_scope() as s:
        daily = int(s.scalar(
            select(func.count(MockExamSession.id)).where(
                MockExamSession.visitor_hash == visitor_hash,
                MockExamSession.started_at >= day_start,
            )
        ) or 0)
        active = int(s.scalar(
            select(func.count(MockExamSession.id)).where(
                MockExamSession.status.in_(["active", "scoring"]),
                MockExamSession.updated_at >= recovery_cutoff,
            )
        ) or 0)
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
        recovery_cutoff = now - timedelta(minutes=cfg.MOCK_EXAM_RECOVERY_TTL_MINUTES)
        daily = int(s.scalar(select(func.count(MockExamSession.id)).where(
            MockExamSession.visitor_hash == visitor_hash,
            MockExamSession.started_at >= day_start,
        )) or 0)
        active = int(s.scalar(select(func.count(MockExamSession.id)).where(
            MockExamSession.status.in_(["active", "scoring"]),
            MockExamSession.updated_at >= recovery_cutoff,
        )) or 0)
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


def checkpoint(session_id: str, *, next_question: int, status: str = "active") -> None:
    with session_scope() as s:
        s.execute(
            update(MockExamSession)
            .where(MockExamSession.id == _uuid(session_id))
            .values(
                current_question=min(next_question, 11),
                status=status,
                retry_question=None,
                updated_at=_now(),
                completed_at=_now() if status == "completed" else None,
            )
        )


def mark_technical_retry(session_id: str, question_number: int) -> None:
    with session_scope() as s:
        s.execute(
            update(MockExamSession)
            .where(MockExamSession.id == _uuid(session_id))
            .values(
                retry_question=question_number,
                current_question=question_number,
                status="active",
                updated_at=_now(),
            )
        )


def create_response(
    *, session_id: str, question_number: int, encrypted_audio_path: str
) -> str:
    expires = _now() + timedelta(hours=get_settings().MOCK_EXAM_AUDIO_TTL_HOURS)
    with session_scope() as s:
        existing = s.scalars(
            select(MockExamResponse).where(
                MockExamResponse.session_id == _uuid(session_id),
                MockExamResponse.question_number == question_number,
            )
        ).first()
        if existing is not None:
            if existing.status != "technical_error":
                raise ValueError("response already submitted")
            existing.status = "queued"
            existing.encrypted_audio_path = encrypted_audio_path
            existing.error_code = None
            existing.updated_at = _now()
            return str(existing.id)
        row = MockExamResponse(
            session_id=_uuid(session_id),
            question_number=question_number,
            encrypted_audio_path=encrypted_audio_path,
            expires_at=expires,
        )
        s.add(row)
        s.flush()
        return str(row.id)


def update_response(
    response_id: str,
    *,
    status: str,
    transcript: str | None = None,
    audio_metrics: dict | None = None,
    language_evaluation: dict | None = None,
    task_evaluation: dict | None = None,
    error_code: str | None = None,
) -> None:
    values: dict[str, Any] = {"status": status, "updated_at": _now(), "error_code": error_code}
    for key, value in {
        "transcript": transcript,
        "audio_metrics": audio_metrics,
        "language_evaluation": language_evaluation,
        "task_evaluation": task_evaluation,
    }.items():
        if value is not None:
            values[key] = value
    with session_scope() as s:
        s.execute(update(MockExamResponse).where(MockExamResponse.id == _uuid(response_id)).values(**values))


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
                "encrypted_audio_path": row.encrypted_audio_path,
                "transcript": row.transcript or "",
                "audio_metrics": row.audio_metrics or {},
                "language_evaluation": row.language_evaluation or {},
                "task_evaluation": row.task_evaluation or {},
                "error_code": row.error_code,
            }
            for row in rows
        ]


def save_report(session_id: str, *, status: str, payload: dict[str, Any]) -> None:
    sid = _uuid(session_id)
    expires = _now() + timedelta(hours=get_settings().MOCK_EXAM_RESULT_TTL_HOURS)
    with session_scope() as s:
        session = s.get(MockExamSession, sid)
        if session is None:
            raise ValueError("mock exam session not found")
        row = s.scalars(select(MockExamReport).where(MockExamReport.session_id == sid)).first()
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
            "expires_at": expires,
        }
        if row is None:
            row = MockExamReport(session_id=sid, created_at=_now(), **values)
            s.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        if status == "completed":
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
            "status": row.status,
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


def cleanup_expired(audio_delete) -> int:
    """Aggregate non-identifying counters, delete audio, then cascade-delete expired sessions."""
    now = _now()
    removed = 0
    with session_scope() as s:
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
            agg.started_count += 1
            if session.status == "completed":
                agg.completed_count += 1
            agg.updated_at = now
            for response in session.responses:
                audio_delete(response.encrypted_audio_path)
            s.delete(session)
            removed += 1
        # Defensive cleanup for already detached records.
        s.execute(delete(MockExamReport).where(MockExamReport.expires_at < now))
        s.execute(delete(MockExamResponse).where(MockExamResponse.expires_at < now))
    return removed
