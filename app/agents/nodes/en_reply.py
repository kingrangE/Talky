"""영어 입력 → 영어 응답."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents.state import GraphState
from app.llm.factory import get_chat_llm


def en_reply_node(state: GraphState) -> dict:
    llm = get_chat_llm()
    msgs: list = [SystemMessage(content=state["system_prompt"])]
    for m in state.get("history", []):
        if m["role"] == "user":
            msgs.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            msgs.append(AIMessage(content=m["content"]))
    msgs.append(HumanMessage(content=state["user_text"]))

    response = llm.invoke(msgs)
    return {"ai_reply": response.content}
