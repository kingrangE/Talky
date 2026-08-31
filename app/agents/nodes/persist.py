"""turn 의 결과를 Postgres + Neo4j 에 저장하는 노드.

graph 흐름 마지막에서 호출되며, 실패해도 graph 자체는 계속 진행되도록
ingest_turn 은 내부에서 swallow.
"""

from __future__ import annotations

from app.agents.state import GraphState
from app.db.repo import save_message
from app.graph_db.ingest import ingest_topics, ingest_turn


def persist_node(state: GraphState) -> dict:
    cid = state.get("conversation_id")
    user_text = (state.get("user_text") or "").strip()
    ai_reply = (state.get("ai_reply") or "").strip()
    if not cid or not user_text or not ai_reply:
        return {}

    language = state.get("language")
    eng_expr = state.get("english_expression")
    bet_expr = state.get("better_expression")

    user_msg_id = save_message(
        cid, "user", user_text, language=language, english_expression=eng_expr
    )
    assistant_msg_id = save_message(
        cid, "assistant", ai_reply, language=language, better_expression=bet_expr
    )

    ingest_turn(
        conversation_id=cid,
        user_id=state["user_id"],
        user_msg_id=user_msg_id,
        user_text=user_text,
        language=language,
        assistant_msg_id=assistant_msg_id,
        ai_text=ai_reply,
        english_expression=eng_expr,
        better_expression=bet_expr,
    )
    # 종료 버튼을 누르지 않은 대화도 다음 대화에서 회상할 수 있도록 현재
    # 발화에서 추출한 토픽을 turn 단위로 즉시 연결한다.
    topics = state.get("memory_topics") or []
    if topics:
        ingest_topics(conversation_id=cid, topics=topics)

    return {
        "user_message_id": user_msg_id,
        "assistant_message_id": assistant_msg_id,
    }
