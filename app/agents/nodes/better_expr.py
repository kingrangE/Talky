"""영어 입력에 대한 더 자연스러운 표현 제안."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.state import GraphState
from app.llm.factory import get_structured_llm

BETTER_EXPR_PROMPT = """You are an English coach. Rewrite the user's most recent English sentence in a more natural / idiomatic way while keeping the meaning identical.

Output JSON with:
- suggestion: the rewritten English sentence (one line)
- note: a short Korean explanation of what changed and why (one or two sentences)

If the original sentence is already natural, set suggestion to the original sentence and note to "이미 자연스러워요.".
"""


class BetterExpressionOutput(BaseModel):
    suggestion: str = Field(description="더 자연스러운 영어 표현")
    note: str = Field(description="짧은 한국어 설명")


def better_expr_node(state: GraphState) -> dict:
    llm = get_structured_llm(BetterExpressionOutput)
    out: BetterExpressionOutput = llm.invoke(
        [
            SystemMessage(content=BETTER_EXPR_PROMPT),
            HumanMessage(content=state["user_text"]),
        ]
    )
    combined = f"**Try:** {out.suggestion}\n\n*{out.note}*"
    return {"better_expression": combined}
