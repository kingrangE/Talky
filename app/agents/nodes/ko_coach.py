"""한국어 입력 → 영어 native 응답 + 사용자 발화의 원어민 영어 표현."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.state import GraphState
from app.llm.factory import get_structured_llm
from app.llm.prompt_registry import builtin_text, invoke_with_prompt
from app.runtime import bounded_messages, format_memory

KO_COACH_SUFFIX = builtin_text("talky/ko-coach")

MEMORY_HEADER = "\n\n[참고할 이전 대화 메모리]\n"


class KoCoachOutput(BaseModel):
    reply: str = Field(description="native English conversational reply")
    english_expression: str = Field(
        description="user's last Korean utterance rewritten as a native, idiomatic English line"
    )


def _build_messages(state: GraphState) -> list:
    suffix = state.get("common_prompt_texts", {}).get("talky/ko-coach", KO_COACH_SUFFIX)
    system = state["system_prompt"] + "\n\n" + suffix
    system += format_memory(state.get("retrieved_memory") or [], MEMORY_HEADER)

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
    out: KoCoachOutput = invoke_with_prompt(llm, bounded_messages(_build_messages(state)), state, "talky/ko-coach")
    return {
        "ai_reply": out.reply,
        "english_expression": out.english_expression,
    }
