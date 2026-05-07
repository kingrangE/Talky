"""DB 의 활성 system prompt 를 로드."""

from __future__ import annotations

from app.db.repo import get_active_prompt


class ActivePromptMissing(RuntimeError):
    pass


def load_active_system_prompt() -> dict:
    prompt = get_active_prompt()
    if prompt is None:
        raise ActivePromptMissing(
            "활성 system prompt 가 없습니다. `python -m app.db.seed` 를 먼저 실행하세요."
        )
    return prompt
