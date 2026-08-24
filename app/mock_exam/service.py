from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from functools import lru_cache
from typing import Any

from app.audio.stt_engine import transcribe_detailed
from app.mock_exam.content import load_exam_set
from app.mock_exam.privacy import EncryptedAudioStore
from app.mock_exam.repository import (
    checkpoint,
    cleanup_expired,
    create_response,
    get_session,
    list_responses,
    mark_technical_retry,
    save_report,
    update_response,
)
from app.mock_exam.scoring import aggregate_report
from app.mock_exam.scoring_graph import build_scoring_graph


@lru_cache(maxsize=1)
def audio_store() -> EncryptedAudioStore:
    return EncryptedAudioStore()


@lru_cache(maxsize=1)
def _executor() -> ThreadPoolExecutor:
    # The public demo intentionally permits only one resource-heavy scoring job.
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="mock-exam")


_futures: dict[str, Future] = {}
_future_lock = threading.Lock()


def run_expiry_cleanup() -> int:
    return cleanup_expired(audio_store().delete)


def _remember(key: str, future: Future) -> None:
    with _future_lock:
        _futures[key] = future

    def done(_: Future) -> None:
        with _future_lock:
            _futures.pop(key, None)

    future.add_done_callback(done)


def active_job_count() -> int:
    with _future_lock:
        return sum(not future.done() for future in _futures.values())


def submit_response(
    *,
    session_id: str,
    question_number: int,
    audio_bytes: bytes,
    client_metrics: dict[str, Any],
) -> str:
    session = get_session(session_id)
    if session is None or session["status"] != "active":
        raise ValueError("mock exam session is not active")
    if question_number != int(session["current_question"]) and question_number != session.get("retry_question"):
        raise ValueError("question is not the active checkpoint")
    if len(audio_bytes) < 128:
        mark_technical_retry(session_id, question_number)
        raise ValueError("audio upload is empty")

    path = audio_store().save(session_id, question_number, audio_bytes)
    response_id = create_response(
        session_id=session_id,
        question_number=question_number,
        encrypted_audio_path=path,
    )
    future = _executor().submit(
        _process_response,
        response_id,
        session_id,
        question_number,
        path,
        client_metrics,
    )
    _remember(f"response:{response_id}", future)
    return response_id


def _advance_after_response(session_id: str, question_number: int) -> None:
    """Advance only after STT has resolved the response.

    This prevents a late technical failure from racing with the next question.
    """
    if question_number < 11:
        checkpoint(session_id, next_question=question_number + 1)
        return
    checkpoint(session_id, next_question=11, status="scoring")
    save_report(session_id, status="queued", payload={"scoring_version": "beta-1"})
    final_future = _executor().submit(_finalize_report, session_id)
    _remember(f"report:{session_id}", final_future)


def _process_response(
    response_id: str,
    session_id: str,
    question_number: int,
    encrypted_path: str,
    client_metrics: dict[str, Any],
) -> None:
    update_response(response_id, status="processing")
    try:
        audio = audio_store().load(encrypted_path)
        detail = transcribe_detailed(audio)
        metrics = {**detail, "browser": client_metrics}
        signal_detected = bool(client_metrics.get("signal_detected", True))
        if not detail["text"] and signal_detected:
            # The upload was valid but STT could not decode speech: allow one technical retry.
            update_response(
                response_id,
                status="technical_error",
                audio_metrics=metrics,
                error_code="stt_empty_with_signal",
            )
            mark_technical_retry(session_id, question_number)
            return
        if not detail["text"]:
            update_response(response_id, status="no_response", transcript="", audio_metrics=metrics)
            _advance_after_response(session_id, question_number)
            return
        update_response(
            response_id,
            status="scored",
            transcript=detail["text"],
            audio_metrics=metrics,
        )
        _advance_after_response(session_id, question_number)
    except Exception as exc:
        update_response(
            response_id,
            status="technical_error",
            error_code=f"processing_{type(exc).__name__}",
        )
        mark_technical_retry(session_id, question_number)


def _finalize_report(session_id: str) -> None:
    session = get_session(session_id)
    if session is None:
        return
    responses = list_responses(session_id)
    unresolved = [
        row for row in responses if row["status"] in {"queued", "processing", "technical_error"}
    ]
    # A technical failure never becomes a zero. Produce a partial report and expose the gap.
    result = build_scoring_graph().invoke({
        "session_id": session_id,
        "exam_set_id": session["exam_set_id"],
        "scoring_profile": session["scoring_profile"],
        "responses": responses,
    })
    language_items = result.get("language_items", {})
    task_items = result.get("task_items", {})
    for row in responses:
        number = int(row["question_number"])
        if row["status"] in {"scored", "no_response"}:
            update_response(
                row["id"],
                status=row["status"],
                language_evaluation=language_items.get(number, {}),
                task_evaluation=task_items.get(number, {}),
            )
    save_report(
        session_id,
        status="partial" if unresolved else "completed",
        payload=result["report"],
    )


def retry_report(session_id: str) -> None:
    future = _executor().submit(_finalize_report, session_id)
    _remember(f"report:{session_id}", future)


def publish_timeout_report(session_id: str) -> None:
    """Publish a deterministic partial report when rubric agents exceed the UI SLA."""
    session = get_session(session_id)
    if session is None or session["status"] != "scoring":
        return
    exam = load_exam_set(session["exam_set_id"])
    report = aggregate_report(
        exam,
        list_responses(session_id),
        {},
        {},
        scoring_profile=session["scoring_profile"],
        disagreement=True,
    )
    report["partial_reason"] = (
        "상세 루브릭 에이전트가 제한 시간 안에 끝나지 않아 현재 이용 가능한 근거로 생성한 임시 결과입니다."
    )
    report["priority_improvements"] = []
    save_report(session_id, status="partial", payload=report)
