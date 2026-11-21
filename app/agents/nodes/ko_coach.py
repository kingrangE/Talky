"""한국어·한영 혼합 입력 → 영어 응답 + 전체 발화의 원어민 영어 표현."""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.state import GraphState
from app.llm.factory import get_structured_llm
from app.llm.prompt_registry import builtin_text, invoke_with_prompt
from app.llm.conversation_style import FRIENDLY_REPLY_STYLE, NATIVE_ENGLISH_STYLE, rewrite_fidelity
from app.llm.rewrite_protection import prepare_rewrite, restore_rewrite
from app.runtime import bounded_messages, format_memory

KO_COACH_SUFFIX = builtin_text("talky/ko-coach")

MEMORY_HEADER = "\n\n[참고할 이전 대화 메모리]\n"


class KoCoachOutput(BaseModel):
    reply: str = Field(description="native English conversational reply")
    english_expression: str = Field(
        description="The user's ENTIRE latest message rewritten in natural English, including both English and Korean portions. Preserve all supported facts, subjects, comparisons, time, quantity, negation and uncertainty. Never invent technical names or skills to replace unclear fragments. Copy protected name markers exactly; do not infer their meaning."
    )


def _build_messages(state: GraphState) -> list:
    suffix = state.get("common_prompt_texts", {}).get("talky/ko-coach", KO_COACH_SUFFIX)
    system = state["system_prompt"] + "\n\n" + suffix
    system += '\n\nFor english_expression, rewrite the ENTIRE latest user message as natural spoken English, including every English and Korean portion in its original order. Correct grammar and improve phrasing in the existing English as well as translating the Korean. Never omit an English sentence merely because it is already English. Preserve all facts, events, quantities, time relationships, negation, intensity and intent; use history only as context, not extra text to rewrite. Before returning, check that each clause of the latest message is represented. Keep this full rewrite separate from reply, which continues the conversation.'
    system += format_memory(state.get("retrieved_memory") or [], MEMORY_HEADER)
    system += NATIVE_ENGLISH_STYLE + FRIENDLY_REPLY_STYLE
    system += rewrite_fidelity(speech=bool(state.get('audio_bytes')))
    if state.get('_rewrite_names'):
        system += '\nLiteral name key (for grammar and pronunciation only): ' + json.dumps(state['_rewrite_names'], ensure_ascii=False)
        system += '\nMandatory copy counts in the English rewrite: ' + json.dumps({marker:state['user_text'].count(marker) for marker in state['_rewrite_names']})
        system += '\nCopy each occurrence in its original clause. Do not turn repeated named subjects into pronouns: the learner may be comparing their own app with a different named app, and a pronoun changes whose facts are stated. Never translate or identify an unfamiliar literal name. Preserve its marker even in needs_improvement=false output.'


    msgs = [SystemMessage(content=system)]
    for m in state.get("history", []):
        if m["role"] == "user":
            msgs.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            msgs.append(AIMessage(content=m["content"]))
    msgs.append(HumanMessage(content=state["user_text"]))
    return msgs


def ko_coach_node(state: GraphState) -> dict:
    prepared = prepare_rewrite(state)
    llm = get_structured_llm(KoCoachOutput)
    out: KoCoachOutput = invoke_with_prompt(llm, bounded_messages(_build_messages(prepared.state)), state, "talky/ko-coach")
    out = restore_rewrite(out, prepared)
    if prepared.repairs:
        assumptions = ' '.join(f'I interpreted "{repair.source}" as "{repair.replacement}" from the context.' for repair in prepared.repairs)
        out.reply = assumptions + ' Please let me know if you meant something else. ' + out.reply
    return {
        "ai_reply": out.reply,
        "english_expression": out.english_expression,
    }
