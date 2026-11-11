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
    if state.get("correction_status") == "failed":
        bet_expr = "표현 교정을 완료하지 못했습니다. 대화 답변은 정상적으로 생성되었습니다."

    user_msg_id, assistant_msg_id = save_turn(
        cid, state["user_id"], user_text, ai_reply, language=language,
        english_expression=eng_expr, better_expression=bet_expr,
        memory_topics=state.get('memory_topics') or [],
        defer_topics=state.get('memory_topics_pending',False),
        request_id=state.get('request_id'), expected_attempt=state.get('turn_attempt'),
    )

    return {
        "user_message_id": user_msg_id,
        "assistant_message_id": assistant_msg_id,
    }


def persist_correction_node(state: GraphState) -> dict:
    from app.db.repo import save_correction
    expression=state.get('better_expression')
    if state.get('correction_status')=='failed':
        expression='표현 교정을 완료하지 못했습니다. 대화 답변은 정상적으로 생성되었습니다.'
    save_correction(conversation_id=state['conversation_id'],user_id=state['user_id'],
        user_message_id=state['user_message_id'],assistant_message_id=state['assistant_message_id'],
        better_expression=expression,request_id=state.get('request_id'),expected_attempt=state.get('turn_attempt'))
    return {}
