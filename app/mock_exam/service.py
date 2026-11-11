from __future__ import annotations

import logging
import math
import time
from typing import Any

from app.audio.stt_engine import transcribe_detailed
from app.audio.validation import validate_audio
from app.mock_exam.content import load_exam_set
from app.mock_exam.privacy import audio_store
from app.mock_exam.repository import (
    cleanup_expired, complete_response, create_response, get_session, list_responses,
    queue_audio_deletion, referenced_audio_paths, requeue_report, save_report,
)
from app.mock_exam.scoring import aggregate_report
from app.mock_exam.scoring_graph import build_scoring_graph

log = logging.getLogger(__name__)


def run_expiry_cleanup() -> int:
    from datetime import datetime, timezone
    from sqlalchemy import select, or_
    from app.db.models import ConversationTurn, PendingAudioDeletion
    from app.db.postgres import session_scope
    store = audio_store()
    with session_scope() as session:
        for turn in session.scalars(select(ConversationTurn).where(
                ConversationTurn.audio_expires_at <= datetime.now(timezone.utc),
                or_(ConversationTurn.input_audio_path.is_not(None), ConversationTurn.output_audio_path.is_not(None)))
                .order_by(ConversationTurn.audio_expires_at).with_for_update(skip_locked=True).limit(1000)):
            for path in (turn.input_audio_path, turn.output_audio_path):
                if path:
                    session.merge(PendingAudioDeletion(path=path))
            turn.input_audio_path=turn.output_audio_path=None
    removed = cleanup_expired(store.delete)
    referenced = referenced_audio_paths()
    # A crash between file creation and DB commit can leave an unreferenced upload.
    cutoff = time.time() - 3600
    for relative in store.older_than(cutoff):
        if relative not in referenced:
            store.delete(relative)
    return removed


def submit_response(
    *, session_id: str, question_number: int, audio_bytes: bytes,
    client_metrics: dict[str, Any],
) -> str:
    session = get_session(session_id)
    if session is None or session["status"] != "active":
        raise ValueError("mock exam session is not active")
    if question_number != int(session["current_question"]):
        raise ValueError("question is not the active checkpoint")
    exam = load_exam_set(session["exam_set_id"])
    duration = validate_audio(audio_bytes, max_seconds=exam.questions[question_number - 1].response_seconds + 3)
    peak = client_metrics.get("peak_rms")
    metrics = {
        "duration_seconds": duration,
        "signal_detected": client_metrics.get("signal_detected") is not False,
        "peak_rms": peak if isinstance(peak, (int, float)) and math.isfinite(peak) and 0 <= peak <= 1 else None,
    }
    path = audio_store().save(session_id, question_number, audio_bytes)
    try:
        return create_response(
            session_id=session_id, question_number=question_number,
            encrypted_audio_path=path, client_metrics=metrics,
        )
    except Exception:
        try:
            audio_store().delete(path)
        except OSError:
            try:
                queue_audio_deletion(path)
            except Exception:
                log.exception("upload cleanup deferred to orphan sweep")
        raise


def _process_response(
    response_id: str, session_id: str, question_number: int,
    encrypted_path: str, client_metrics: dict[str, Any],
    attempt: int,
) -> None:
    detail = transcribe_detailed(audio_store().load(encrypted_path))
    metrics = {**detail, "browser": client_metrics}
    signal_detected = bool(client_metrics.get("signal_detected", True))
    status = "scored" if detail["text"] else "technical_error" if signal_detected else "no_response"
    complete_response(
        response_id, status=status, transcript=detail["text"], audio_metrics=metrics,
        error_code="stt_empty_with_signal" if status == "technical_error" else None,
        expected_attempt=attempt,
    )


def _finalize_report(session_id: str, attempt: int) -> None:
    session = get_session(session_id)
    if session is None:
        return
    responses = list_responses(session_id)
    unresolved = len(responses) != 11 or any(
        row["status"] not in {"scored", "no_response"} for row in responses
    )
    result = build_scoring_graph().invoke({
        "session_id": session_id, "exam_set_id": session["exam_set_id"],
        "scoring_profile": session["scoring_profile"], "responses": responses,
    })
    report = result["report"]
    if unresolved:
        report["partial_reason"] = "일부 응답이 미처리 상태인 임시 결과입니다."
    save_report(
        session_id,
        status="partial" if unresolved or report.get("partial_reason") else "completed",
        payload=report,
        expected_attempt=attempt,
        evaluations=[{
            "id": row["id"],
            "language_evaluation": result.get("language_items", {}).get(int(row["question_number"]), {}),
            "task_evaluation": result.get("task_items", {}).get(int(row["question_number"]), {}),
        } for row in responses if row["status"] in {"scored", "no_response"}],
    )


def retry_report(session_id: str) -> None:
    requeue_report(session_id)


def publish_timeout_report(session_id: str) -> None:
    session = get_session(session_id)
    if session is None or session["status"] != "scoring":
        return
    report = aggregate_report(
        load_exam_set(session["exam_set_id"]), list_responses(session_id), {}, {},
        scoring_profile=session["scoring_profile"], disagreement=True,
    )
    report["partial_reason"] = "상세 평가가 지연되어 현재 근거로 생성한 임시 결과입니다."
    report["priority_improvements"] = []
    save_report(session_id, status="partial", payload=report, final=False)
