"""한국어·한영 혼합 입력 → 영어 응답 + 전체 발화의 원어민 영어 표현."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.state import GraphState
from app.llm.factory import get_structured_llm
from app.llm.prompt_registry import builtin_text, invoke_with_prompt
from app.llm.conversation_style import NATIVE_ENGLISH_STYLE
from app.runtime import bounded_messages, format_memory

KO_COACH_SUFFIX = builtin_text("talky/ko-coach")

MEMORY_HEADER = "\n\n[참고할 이전 대화 메모리]\n"


class KoCoachOutput(BaseModel):
    reply: str = Field(description="native English conversational reply")
    english_expression: str = Field(
        description="The user's ENTIRE latest message rewritten in natural, idiomatic English. Include both existing English and Korean portions of mixed-language input, correcting the English too. Preserve every fact, time, quantity, negation and intent; never translate only the Korean portion."
    )


def _build_messages(state: GraphState) -> list:
    suffix = state.get("common_prompt_texts", {}).get("talky/ko-coach", KO_COACH_SUFFIX)
    system = state["system_prompt"] + "\n\n" + suffix
    system += '\n\nFor english_expression, rewrite the ENTIRE latest user message as natural spoken English, including every English and Korean portion in its original order. Correct grammar and improve phrasing in the existing English as well as translating the Korean. Never omit an English sentence merely because it is already English. Preserve all facts, events, quantities, time relationships, negation, intensity and intent; use history only as context, not extra text to rewrite. Before returning, check that each clause of the latest message is represented. Keep this full rewrite separate from reply, which continues the conversation.'
    system += format_memory(state.get("retrieved_memory") or [], MEMORY_HEADER)
    system += NATIVE_ENGLISH_STYLE

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
