"""영어 입력 → 영어 응답."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents.state import GraphState
from app.llm.factory import get_chat_llm
from app.llm.prompt_registry import builtin_text, invoke_with_prompt
from app.runtime import bounded_messages, format_memory

NATIVE_SUFFIX = builtin_text("talky/en-reply")

MEMORY_HEADER = "\n\n[Earlier related memory]\n"


def _build_messages(state: GraphState) -> list:
    system = (
        state["system_prompt"]
        + state.get("common_prompt_texts", {}).get("talky/en-reply", NATIVE_SUFFIX)
        + format_memory(state.get("retrieved_memory") or [], MEMORY_HEADER)
    )
    msgs: list = [SystemMessage(content=system)]
    for m in state.get("history", []):
        if m["role"] == "user":
            msgs.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            msgs.append(AIMessage(content=m["content"]))
    msgs.append(HumanMessage(content=state["user_text"]))
    return msgs


def en_reply_node(state: GraphState) -> dict:
    llm = get_chat_llm()
    response = invoke_with_prompt(llm, bounded_messages(_build_messages(state)), state, "talky/en-reply")
    return {"ai_reply": response.content}
