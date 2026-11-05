"""DB 의 활성 system prompt 를 로드."""

from __future__ import annotations

from app.db.repo import get_active_prompt, get_conversation_prompt


class ActivePromptMissing(RuntimeError):
    pass


def load_active_system_prompt(user_id: str) -> dict:
    prompt = get_active_prompt(user_id)
    if prompt is None:
        raise ActivePromptMissing(
            "활성 system prompt 가 없습니다. `python -m app.db.seed` 를 먼저 실행하세요."
        )
    return prompt


def load_conversation_prompt_node(state: dict) -> dict:
    """호출자가 전달한 본문 대신 RDB의 사용자 소유 대화 프롬프트를 사용한다."""
    prompt = get_conversation_prompt(state["conversation_id"], state["user_id"])
    if prompt is None:
        raise ActivePromptMissing("사용자 소유의 대화 system prompt가 없습니다.")
    return {"prompt_version_id": prompt["id"], "system_prompt": prompt["content"]}
