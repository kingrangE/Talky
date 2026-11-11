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
    user_message_id: Optional[str]
    assistant_message_id: Optional[str]

    # 입력
    audio_bytes: Optional[bytes]
    user_text: str
    language: Optional[Literal["ko", "en"]]
    history: list[HistoryItem]
    system_prompt: str
    system_prompt_ref: dict | None
    common_prompt_refs: dict
    common_prompt_texts: dict[str, str]
    trace_id: str
    request_id: str
    turn_attempt: int

    # 회상 (Phase E)
    retrieved_memory: list[dict[str, Any]]
    memory_topics: list[str]
    memory_recall_decision: str

    # 출력
    ai_reply: str
    english_expression: Optional[str]
    correction_status: str
    better_expression: Optional[str]
    audio_reply: Optional[bytes]
