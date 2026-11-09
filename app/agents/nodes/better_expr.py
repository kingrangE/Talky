"""영어 입력에 대한 더 원어민스러운 표현 제안."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.state import GraphState
from app.llm.factory import get_structured_llm
from app.llm.prompt_registry import builtin_text, invoke_with_prompt
from app.runtime import bounded_messages

BETTER_EXPR_PROMPT = builtin_text("talky/better-expression")


class BetterExpressionOutput(BaseModel):
    suggestion: str = Field(description="native-sounding English rewrite")
    note: str = Field(description="짧은 한국어 설명 (왜 더 원어민스러운지)")


def _build_messages(state: GraphState) -> list:
    return [
        SystemMessage(content=state.get("common_prompt_texts", {}).get("talky/better-expression", BETTER_EXPR_PROMPT)),
        HumanMessage(content=state["user_text"]),
    ]


def better_expr_node(state: GraphState) -> dict:
    llm = get_structured_llm(BetterExpressionOutput)
    out: BetterExpressionOutput = invoke_with_prompt(llm, bounded_messages(_build_messages(state)), state, "talky/better-expression")
    combined = f"**Try:** {out.suggestion}\n\n*{out.note}*"
    return {"better_expression": combined}
