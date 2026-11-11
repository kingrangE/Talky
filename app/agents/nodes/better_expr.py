"""영어 입력에 대한 더 원어민스러운 표현 제안."""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.state import GraphState
from app.llm.factory import get_structured_llm
from app.llm.prompt_registry import builtin_text, invoke_with_prompt
from app.runtime import bounded_history, bounded_messages

BETTER_EXPR_PROMPT = builtin_text("talky/better-expression")


class BetterExpressionOutput(BaseModel):
    suggestion: str = Field(description="native-sounding English rewrite")
    note: str = Field(description="짧은 한국어 설명 (왜 더 원어민스러운지)")


def _build_messages(state: GraphState) -> list:
    messages = [
        SystemMessage(content=state.get("common_prompt_texts", {}).get("talky/better-expression", BETTER_EXPR_PROMPT)),
    ]
    for message in bounded_history(state.get('history') or []):
        cls = HumanMessage if message['role'] == 'user' else AIMessage
        messages.append(cls(content=message['content']))
    messages.append(HumanMessage(content=state['user_text']))
    return messages


def better_expr_node(state: GraphState) -> dict:
    try:
        llm = get_structured_llm(BetterExpressionOutput)
        out: BetterExpressionOutput = invoke_with_prompt(llm, bounded_messages(_build_messages(state)), state, "talky/better-expression")
    except Exception as exc:
        logging.getLogger('talky.metrics').warning('correction_unavailable error_type=%s', type(exc).__name__)
        return {"better_expression": None, "correction_status": "failed"}
    combined = f"**Try:** {out.suggestion}\n\n*{out.note}*"
    return {"better_expression": combined, "correction_status": "completed"}
