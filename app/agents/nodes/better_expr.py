"""영어 입력에 대한 더 원어민스러운 표현 제안."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.state import GraphState
from app.llm.factory import get_structured_llm

BETTER_EXPR_PROMPT = """You are a native English speaker. Rewrite the user's most recent English sentence into something a native speaker would actually say in casual, everyday conversation.

Rules:
- Use contractions, idioms, and current everyday phrasing.
- Avoid textbook English, overly formal phrasing, or stiff grammar.
- Keep the meaning identical, just make it sound natural and native.

Output JSON with:
- suggestion: the native-sounding rewrite (one line)
- note: a short Korean explanation of what changed and why it sounds more natural to a native speaker (one or two sentences)

If the original sentence already sounds fully native, set suggestion to the original sentence and note to "이미 원어민 같은 표현이에요.".
"""


class BetterExpressionOutput(BaseModel):
    suggestion: str = Field(description="native-sounding English rewrite")
    note: str = Field(description="짧은 한국어 설명 (왜 더 원어민스러운지)")


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
