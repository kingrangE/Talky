"""Turn 단위 LangGraph.

load_prompt → STT → memory_recall → router → (ko_coach | en_reply → better_expr) → persist → TTS
이후 Phase F 에서 ending_graph 가 별도로 추가됨.
"""

from __future__ import annotations

from app.telemetry import timed_stage
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
from app.llm.prompts import load_conversation_prompt_node


@lru_cache(maxsize=1)
def build_conversation_graph():
    g = StateGraph(GraphState)
    g.add_node("load_prompt", timed_stage("load_prompt", load_conversation_prompt_node))
    g.add_node("stt", timed_stage("stt", stt_node))
    g.add_node("memory_recall", timed_stage("memory_recall", memory_recall_node))
    g.add_node("ko_coach", timed_stage("ko_coach", ko_coach_node))
    g.add_node("en_reply", timed_stage("en_reply", en_reply_node))
    g.add_node("better_expr", timed_stage("better_expr", better_expr_node))
    g.add_node("tts", timed_stage("tts", tts_node))
    g.add_node("persist", timed_stage("persist", persist_node))

    g.add_edge(START, "load_prompt")
    g.add_edge("load_prompt", "stt")
    g.add_edge("stt", "memory_recall")
    g.add_conditional_edges(
        "memory_recall", route_by_lang, {"ko": "ko_coach", "en": "en_reply"}
    )
    g.add_edge("ko_coach", "persist")
    g.add_edge("en_reply", "better_expr")
    g.add_edge("better_expr", "persist")
    g.add_edge("persist", "tts")
    g.add_edge("tts", END)
    return g.compile()
