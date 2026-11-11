"""Bound model input and share bounded inference slots across app and worker processes."""

from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import or_, select, text

from app.db.postgres import get_engine
from app.db.models import MockExamReport, MockExamResponse, MockExamSession
from app.settings import get_settings

INFERENCE_LOCK = 74201932


class ModelBusy(RuntimeError):
    pass


@contextmanager
def inference_slot(*, for_exam: bool = False):
    with get_engine().connect() as connection:
        acquired_key = None
        for offset in range(get_settings().INFERENCE_CAPACITY):
            key = INFERENCE_LOCK + offset * 100
            acquired = connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
            ).scalar()
            connection.commit()
            if acquired:
                acquired_key = key
                break
        if acquired_key is None:
            raise ModelBusy("다른 음성/모델 작업을 처리 중입니다. 잠시 후 다시 시도하세요.")
        try:
            if not for_exam:
                now = datetime.now(timezone.utc)
                pending = [select(model.id).join(MockExamSession).where(
                    model.status == "queued", model.expires_at > now,
                    MockExamSession.status.in_(["active", "scoring"]),
                    MockExamSession.expires_at > now,
                ).exists() for model in (MockExamResponse, MockExamReport)]
                exam_pending = connection.scalar(select(or_(*pending)))
                connection.commit()
                if exam_pending:
                    raise ModelBusy("시험 응답을 먼저 처리하고 있습니다. 잠시 후 다시 시도하세요.")
            yield
        finally:
            try:
                connection.rollback()
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": acquired_key})
                connection.commit()
            except Exception:
                connection.invalidate()
                raise


def bounded_history(history: list[dict]) -> list[dict]:
    remaining = get_settings().MAX_HISTORY_BYTES
    selected = []
    for message in reversed(history):
        if message.get("role") not in {"user", "assistant"}:
            continue
        content = str(message.get("content") or "")
        size = len(content.encode("utf-8")) + 16
        if size > remaining:
            break
        selected.append({"role": message["role"], "content": content})
        remaining -= size
    selected.reverse()
    if selected and selected[0]["role"] == "assistant":
        selected.pop(0)
    return selected


def bounded_messages(messages: list) -> list:
    """Keep instructions and current input intact; remove oldest history first."""
    sizes = [len(str(message.content).encode("utf-8")) + 64 for message in messages]
    selected = {index for index, message in enumerate(messages) if message.type == "system"}
    if messages:
        selected.add(len(messages) - 1)
    from app.context_budget import input_budget
    remaining = input_budget() - sum(sizes[index] for index in selected)
    if remaining < 0:
        raise ValueError("모델 입력이 너무 깁니다. 내용을 짧게 나눠 주세요.")
    for index in reversed(range(len(messages))):
        if index in selected:
            continue
        if sizes[index] > remaining:
            break
        selected.add(index)
        remaining -= sizes[index]
    result = [message for index, message in enumerate(messages) if index in selected]
    first_history = next((i for i, message in enumerate(result) if message.type != 'system'), None)
    if first_history is not None and result[first_history].type == 'ai':
        result.pop(first_history)
    return result


def format_memory(memory: list[dict], header: str) -> str:
    remaining = 4000 - len(header.encode("utf-8"))
    lines = []
    for item in memory[:8]:
        text = (item.get("text") or "").replace("\n", " ").strip()
        prefix = f"- ({str(item.get('topic', ''))[:64]}) "
        available = remaining - len(prefix.encode("utf-8")) - 1
        if available <= 0:
            break
        text = text.encode("utf-8")[:available].decode("utf-8", errors="ignore")
        if text:
            line = prefix + text
            lines.append(line)
            remaining -= len(line.encode("utf-8")) + 1
    return header + "\n".join(lines) if lines else ""


# Per-job deadline is independent from the PostgreSQL lease's recovery grace period.
from contextvars import ContextVar
import time
_job_deadline = ContextVar('job_deadline', default=None)


@contextmanager
def job_budget(seconds):
    token = _job_deadline.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _job_deadline.reset(token)


def require_budget(seconds=1):
    deadline = _job_deadline.get()
    if deadline is not None and deadline - time.monotonic() < seconds:
        raise TimeoutError('insufficient remaining job budget')


@contextmanager
def resource_slot(kind, *, max_wait_seconds=None):
    """Wait only around resource use; the job supervisor bounds the full wait."""
    import json
    import logging
    started = time.monotonic()
    base = INFERENCE_LOCK if kind == 'inference' else 74211932
    capacity = get_settings().INFERENCE_CAPACITY if kind == 'inference' else get_settings().AUDIO_CAPACITY
    while True:
        require_budget()
        with get_engine().connect() as connection:
            key = None
            for offset in range(capacity):
                candidate = base + offset * 100
                if connection.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key':candidate}):
                    key = candidate
                    break
            connection.commit()
            if key is not None:
                logging.getLogger('talky.metrics').info(json.dumps({'event':'resource_wait','resource':kind,
                    'duration_ms':round((time.monotonic()-started)*1000,2)}))
                try:
                    yield
                finally:
                    try:
                        connection.rollback()
                        connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key':key})
                        connection.commit()
                    except Exception:
                        connection.invalidate()
                        raise
                return
        limit = get_settings().MOCK_EXAM_JOB_TIMEOUT_SECONDS if max_wait_seconds is None else max_wait_seconds
        if time.monotonic()-started >= limit:
            raise TimeoutError('resource capacity wait expired')
        time.sleep(.05)


def audio_capacity(function):
    from functools import wraps
    @wraps(function)
    def run(*args, **kwargs):
        kind = 'inference' if function.__module__.endswith('stt_engine') and get_settings().WHISPER_DEVICE == 'cuda' else 'audio'
        with resource_slot(kind):
            return function(*args, **kwargs)
    return run
