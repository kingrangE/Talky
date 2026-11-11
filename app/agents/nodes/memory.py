"""메모리 회상 노드.

로컬 A.X가 최근 대화와 발화를 0·1로 판정한다. 불확실하거나 실패하면
기존 사용자 범위 검색을 유지한다.
"""

from __future__ import annotations

import logging
import json

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.state import GraphState
from app.agents.nodes.memory_gate import recall_decision
from app.graph_db.recall import recall_related
from app.llm.factory import get_structured_llm
from app.runtime import bounded_messages, require_budget
from app.settings import get_settings
from app.db.repo import active_conversation_ids

log = logging.getLogger(__name__)

TOPIC_PROMPT = (
    "Extract 1-3 short noun-phrase topics from the user's message. "
    "Each topic should be a single English word or 2-word phrase, all lowercase, "
    "describing the subject (e.g., 'running', 'office work', 'movies'). "
    "If the message is just small talk, return an empty list."
)


class TopicCandidates(BaseModel):
    topics: list[str] = Field(default_factory=list, description="1~3 short topic phrases in english")


def _extract_topics(text: str, raise_errors: bool = False) -> list[str]:
    if not text or len(text) < 4:
        return []
    try:
        llm = get_structured_llm(TopicCandidates, temperature=0.0)
        out: TopicCandidates = llm.invoke(bounded_messages(
            [SystemMessage(content=TOPIC_PROMPT), HumanMessage(content=text)]
        ))
        return [t.strip().lower() for t in out.topics if t and t.strip()][:3]
    except Exception as exc:
        if raise_errors:
            raise
        log.warning("topic extraction failed: %s", exc)
        return []



def memory_recall_node(state: GraphState) -> dict:
    user_text = (state.get("user_text") or "").strip()
    user_id = state.get("user_id")
    if not user_text or not user_id:
        return {"retrieved_memory": []}
    search, reason = recall_decision(user_text, state.get('history') or [])
    log.info(json.dumps({'event': 'memory_gate', 'decision': 'search' if search else 'skip', 'reason': reason}))
    if not search:
        return {'retrieved_memory': [], 'memory_topics': [], 'memory_recall_decision': reason, 'memory_topics_pending': True}
    require_budget(get_settings().LLM_TIMEOUT_SECONDS)
    topics = _extract_topics(user_text)
    memory = recall_related(
        user_id,
        topics,
        current_conversation_id=state.get("conversation_id"),
        query_text=user_text,
    )
    active = active_conversation_ids(user_id, [m.get('conversation_id', '') for m in memory])
    memory = [m for m in memory if m.get('conversation_id') in active]
    return {"retrieved_memory": memory, "memory_topics": topics, "memory_recall_decision": reason, "memory_topics_pending": False}
