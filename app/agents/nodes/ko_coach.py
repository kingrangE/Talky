"""한국어 입력 → 영어 native 응답 + 사용자 발화의 원어민 영어 표현."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.state import GraphState
from app.llm.factory import get_structured_llm

KO_COACH_SUFFIX = """
[중요] 사용자가 방금 한국어로 말했습니다. 다음 JSON 으로 응답하세요.

규칙:
- reply 는 반드시 자연스러운 원어민 영어(native speaker English)로만 작성합니다. 한국어 사용 절대 금지.
- 교과서 영어가 아니라 친구와 캐주얼하게 대화하듯이 contractions, 자연스러운 표현, 일상적인 idioms 를 사용하세요.
- 1~3 문장 + 대화를 이어가는 follow-up 질문 1개로 끝맺으세요.
- english_expression 은 사용자가 방금 말한 한국어 문장을 원어민이 실제로 말할 법한 idiomatic English 한 줄로 표현 (직역 금지, 자연스러운 표현으로).

스키마:
- reply: native English conversational reply (한국어 금지)
- english_expression: 사용자 한국어 문장의 원어민스러운 영어 표현 한 줄
"""

MEMORY_HEADER = "\n\n[참고할 이전 대화 메모리]\n"


class KoCoachOutput(BaseModel):
    reply: str = Field(description="native English conversational reply")
    english_expression: str = Field(
        description="user's last Korean utterance rewritten as a native, idiomatic English line"
    )


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
    llm = get_structured_llm(KoCoachOutput)
    out: KoCoachOutput = llm.invoke(_build_messages(state))
    return {
        "ai_reply": out.reply,
        "english_expression": out.english_expression,
    }
