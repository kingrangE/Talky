"""Turn 단위 LangGraph.

Phase B: START → llm → END (단일 노드).
이후 Phase 별로 stt / memory_recall / router / ko_coach / en_reply / better_expr / tts /
persist 노드를 추가하며 분기/직렬 구조로 확장.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.agents.nodes.llm_node import llm_node
from app.agents.state import GraphState


@lru_cache(maxsize=1)
def build_conversation_graph():
    g = StateGraph(GraphState)
    g.add_node("llm", llm_node)
    g.add_edge(START, "llm")
    g.add_edge("llm", END)
    return g.compile()
