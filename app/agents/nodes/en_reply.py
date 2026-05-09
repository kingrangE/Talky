"""영어 입력 → 영어 응답."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents.state import GraphState
from app.llm.factory import get_chat_llm

NATIVE_SUFFIX = (
    "\n\nReply ONLY in English. Sound like a native speaker chatting with a friend: "
    "contractions, natural fillers, idioms, and current everyday phrasing — not "
    "textbook English. Keep it short (1–3 sentences) and end with a follow-up "
    "question to keep the chat going."
)

MEMORY_HEADER = "\n\n[Earlier related memory]\n"


def _format_memory(memory: list[dict]) -> str:
    if not memory:
        return ""
    lines = []
    for item in memory[:8]:
        topic = item.get("topic", "")
        text = (item.get("text") or "").replace("\n", " ").strip()
        if not text:
            continue
        lines.append(f"- ({topic}) {text}")
    return MEMORY_HEADER + "\n".join(lines) if lines else ""


def en_reply_node(state: GraphState) -> dict:
    llm = get_chat_llm()
    system = (
        state["system_prompt"]
        + NATIVE_SUFFIX
        + _format_memory(state.get("retrieved_memory") or [])
    )
    msgs: list = [SystemMessage(content=system)]
    for m in state.get("history", []):
        if m["role"] == "user":
            msgs.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            msgs.append(AIMessage(content=m["content"]))
    msgs.append(HumanMessage(content=state["user_text"]))

    response = llm.invoke(msgs)
    return {"ai_reply": response.content}
