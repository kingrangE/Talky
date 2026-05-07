"""한국어 입력 → 한국어 응답 + 사용자 발화의 영어 표현."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.state import GraphState
from app.llm.factory import get_chat_llm

KO_COACH_SUFFIX = """
[중요] 사용자가 방금 한국어로 말했습니다. 아래 JSON 스키마로 응답하세요.
- reply: 짧고 자연스러운 한국어 응답 (대화를 이어가는 1~3문장)
- english_expression: 사용자가 방금 말한 한국어 문장을 영어로 자연스럽게 표현한 한 줄
"""

MEMORY_HEADER = "\n\n[참고할 이전 대화 메모리]\n"


class KoCoachOutput(BaseModel):
    reply: str = Field(description="한국어 응답")
    english_expression: str = Field(description="사용자 마지막 한국어 발화의 영어 표현")


def _format_memory(memory: list[dict]) -> str:
    if not memory:
        return ""
    lines = []
    for item in memory[:8]:
        topic = item.get("topic", "")
        text = (item.get("text") or "").replace("\n", " ").strip()
        if not text:
            continue
        lines.append(f"- ({topic}) {text}")
    return MEMORY_HEADER + "\n".join(lines) if lines else ""


def _build_messages(state: GraphState) -> list:
    system = state["system_prompt"] + "\n\n" + KO_COACH_SUFFIX
    system += _format_memory(state.get("retrieved_memory") or [])

    msgs = [SystemMessage(content=system)]
    for m in state.get("history", []):
        if m["role"] == "user":
            msgs.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            msgs.append(AIMessage(content=m["content"]))
    msgs.append(HumanMessage(content=state["user_text"]))
    return msgs


def ko_coach_node(state: GraphState) -> dict:
    llm = get_chat_llm().with_structured_output(KoCoachOutput)
    out: KoCoachOutput = llm.invoke(_build_messages(state))
    return {
        "ai_reply": out.reply,
        "english_expression": out.english_expression,
    }
