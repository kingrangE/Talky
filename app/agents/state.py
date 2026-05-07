"""Talky 의 LangGraph turn-단위 상태."""

from __future__ import annotations

from typing import Any, Literal, Optional, TypedDict


class HistoryItem(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class GraphState(TypedDict, total=False):
    # 식별자
    conversation_id: str
    user_id: str
    prompt_version_id: Optional[str]

    # 입력
    audio_bytes: Optional[bytes]   # Phase C 부터
    user_text: str
    language: Optional[Literal["ko", "en"]]   # Phase C 부터
    history: list[HistoryItem]
    system_prompt: str

    # 회상 (Phase E 부터)
    retrieved_memory: list[dict[str, Any]]

    # 출력
    ai_reply: str
    english_expression: Optional[str]   # Phase D — 한국어 입력 시
    better_expression: Optional[str]    # Phase D — 영어 입력 시
    audio_reply: Optional[bytes]        # Phase C — TTS wav
