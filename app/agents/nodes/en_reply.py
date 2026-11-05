"""영어 입력 → 영어 응답."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents.state import GraphState
from app.llm.factory import get_chat_llm
from app.runtime import bounded_messages, format_memory

NATIVE_SUFFIX = (
    "\n\nReply ONLY in English. Sound like a native speaker chatting with a friend: "
    "contractions, natural fillers, idioms, and current everyday phrasing — not "
    "textbook English. Keep it short (1–3 sentences) and end with a follow-up "
    "question to keep the chat going."
)

MEMORY_HEADER = "\n\n[Earlier related memory]\n"


def en_reply_node(state: GraphState) -> dict:
    llm = get_chat_llm()
    system = (
        state["system_prompt"]
        + NATIVE_SUFFIX
        + format_memory(state.get("retrieved_memory") or [], MEMORY_HEADER)
    )
    msgs: list = [SystemMessage(content=system)]
    for m in state.get("history", []):
        if m["role"] == "user":
            msgs.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            msgs.append(AIMessage(content=m["content"]))
    msgs.append(HumanMessage(content=state["user_text"]))

    response = llm.invoke(bounded_messages(msgs))
    return {"ai_reply": response.content}
