"""Phase B: 단일 LLM 노드.

이후 Phase D 에서 ko_coach / en_reply / better_expr 로 분리됨.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents.state import GraphState
from app.llm.factory import get_chat_llm


def llm_node(state: GraphState) -> dict:
    llm = get_chat_llm()

    messages: list = [SystemMessage(content=state["system_prompt"])]
    for m in state.get("history", []):
        if m["role"] == "user":
            messages.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            messages.append(AIMessage(content=m["content"]))
    messages.append(HumanMessage(content=state["user_text"]))

    response = llm.invoke(messages)
    return {"ai_reply": response.content}
