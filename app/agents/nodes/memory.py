"""메모리 회상 노드.

현재 user_text 에서 1~3 개 토픽 후보를 LLM 으로 추출한 뒤 Neo4j 에서 multi-hop
관련 이전 메시지를 가져와 state.retrieved_memory 에 채운다.
"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.state import GraphState
from app.graph_db.recall import recall_related
from app.llm.factory import get_structured_llm

log = logging.getLogger(__name__)

TOPIC_PROMPT = (
    "Extract 1-3 short noun-phrase topics from the user's message. "
    "Each topic should be a single English word or 2-word phrase, all lowercase, "
    "describing the subject (e.g., 'running', 'office work', 'movies'). "
    "If the message is just small talk, return an empty list."
)


class TopicCandidates(BaseModel):
    topics: list[str] = Field(default_factory=list, description="1~3 short topic phrases in english")


def _extract_topics(text: str) -> list[str]:
    if not text or len(text) < 4:
        return []
    try:
        llm = get_structured_llm(TopicCandidates, temperature=0.0)
        out: TopicCandidates = llm.invoke(
            [SystemMessage(content=TOPIC_PROMPT), HumanMessage(content=text)]
        )
        return [t.strip().lower() for t in out.topics if t and t.strip()][:3]
    except Exception as exc:
        log.warning("topic extraction failed: %s", exc)
        return []


def memory_recall_node(state: GraphState) -> dict:
    user_text = (state.get("user_text") or "").strip()
    user_id = state.get("user_id")
    if not user_text or not user_id:
        return {"retrieved_memory": []}
    topics = _extract_topics(user_text)
    if not topics:
        return {"retrieved_memory": []}
    memory = recall_related(user_id, topics)
    return {"retrieved_memory": memory}
