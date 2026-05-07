"""Turn 단위 LangGraph.

Phase B: START → llm → END
Phase C: START → stt → llm → tts → END
이후 Phase D 에서 router 도입.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.agents.nodes.llm_node import llm_node
from app.agents.nodes.stt import stt_node
from app.agents.nodes.tts import tts_node
from app.agents.state import GraphState


@lru_cache(maxsize=1)
def build_conversation_graph():
    g = StateGraph(GraphState)
    g.add_node("stt", stt_node)
    g.add_node("llm", llm_node)
    g.add_node("tts", tts_node)
    g.add_edge(START, "stt")
    g.add_edge("stt", "llm")
    g.add_edge("llm", "tts")
    g.add_edge("tts", END)
    return g.compile()
