"""Bound model input and share one inference slot across app and worker processes."""

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
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": INFERENCE_LOCK}
        ).scalar()
        connection.commit()
        if not acquired:
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
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": INFERENCE_LOCK})
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
    remaining = get_settings().LLM_MAX_INPUT_BYTES - sum(sizes[index] for index in selected)
    if remaining < 0:
        raise ValueError("모델 입력이 너무 깁니다. 내용을 짧게 나눠 주세요.")
    for index in reversed(range(len(messages))):
        if index in selected:
            continue
        if sizes[index] > remaining:
            break
        selected.add(index)
        remaining -= sizes[index]
    return [message for index, message in enumerate(messages) if index in selected]


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
