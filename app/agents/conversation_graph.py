"""Turn 단위 LangGraph.

Phase E: STT → memory_recall → router → (ko_coach | en_reply → better_expr) → TTS → persist
이후 Phase F 에서 ending_graph 가 별도로 추가됨.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.agents.nodes.better_expr import better_expr_node
from app.agents.nodes.en_reply import en_reply_node
from app.agents.nodes.ko_coach import ko_coach_node
from app.agents.nodes.memory import memory_recall_node
from app.agents.nodes.persist import persist_node
from app.agents.nodes.router import route_by_lang
from app.agents.nodes.stt import stt_node
from app.agents.nodes.tts import tts_node
from app.agents.state import GraphState


@lru_cache(maxsize=1)
def build_conversation_graph():
    g = StateGraph(GraphState)
    g.add_node("stt", stt_node)
    g.add_node("memory_recall", memory_recall_node)
    g.add_node("ko_coach", ko_coach_node)
    g.add_node("en_reply", en_reply_node)
    g.add_node("better_expr", better_expr_node)
    g.add_node("tts", tts_node)
    g.add_node("persist", persist_node)

    g.add_edge(START, "stt")
    g.add_edge("stt", "memory_recall")
    g.add_conditional_edges(
        "memory_recall", route_by_lang, {"ko": "ko_coach", "en": "en_reply"}
    )
    g.add_edge("ko_coach", "tts")
    g.add_edge("en_reply", "better_expr")
    g.add_edge("better_expr", "tts")
    g.add_edge("tts", "persist")
    g.add_edge("persist", END)
    return g.compile()
