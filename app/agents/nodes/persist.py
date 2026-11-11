"""Commit the conversation and its Graph synchronization event atomically."""

from __future__ import annotations

from app.agents.state import GraphState
from app.db.repo import save_turn


def persist_node(state: GraphState) -> dict:
    cid = state.get("conversation_id")
    user_text = (state.get("user_text") or "").strip()
    ai_reply = (state.get("ai_reply") or "").strip()
    if not cid or not user_text or not ai_reply:
        return {}

    language = state.get("language")
    eng_expr = state.get("english_expression")
    bet_expr = state.get("better_expression")

    user_msg_id, assistant_msg_id = save_turn(
        cid, state["user_id"], user_text, ai_reply, language=language,
        english_expression=eng_expr, better_expression=bet_expr,
        memory_topics=state.get('memory_topics') or [],
    )

    return {
        "user_message_id": user_msg_id,
        "assistant_message_id": assistant_msg_id,
    }
