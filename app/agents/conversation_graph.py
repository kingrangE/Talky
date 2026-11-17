"""Turn 단위 LangGraph.

영어 응답 저장 후 TTS·한국어 번역·선택적 표현 교정을 병렬 실행한다.
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
from app.agents.nodes.persist import persist_node, persist_correction_node
from app.agents.nodes.router import route_by_lang
from app.agents.nodes.stt import stt_node
from app.agents.nodes.tts import tts_node
from app.agents.state import GraphState
from app.llm.prompts import load_conversation_prompt_node
from app.conversation_starters import starter_node
from app.reply_translation import translation_node


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
    g.add_node("translation", timed_stage("translation", translation_node))
    g.add_node("persist", timed_stage("persist", persist_node))
    g.add_node("persist_correction", timed_stage("persist_correction", persist_correction_node))
    g.add_node('starter', timed_stage('starter', starter_node))

    g.add_edge(START, "load_prompt")
    g.add_conditional_edges('load_prompt', lambda state: 'starter' if state.get('starter_topic') else 'stt',
                            {'starter': 'starter', 'stt': 'stt'})
    g.add_edge('starter', 'tts')
    g.add_edge('starter', 'translation')
    g.add_edge("stt", "memory_recall")
    g.add_conditional_edges(
        "memory_recall", route_by_lang, {"ko": "ko_coach", "en": "en_reply"}
    )
    g.add_edge("ko_coach", "persist")
    g.add_edge("en_reply", "persist")
    g.add_edge("persist", "tts")
    g.add_edge("persist", "translation")
    g.add_conditional_edges("persist", lambda state: "better_expr" if state.get("language")=="en" else END,
                            {"better_expr":"better_expr", END: END})
    g.add_edge("better_expr", "persist_correction")
    g.add_edge("persist_correction", END)
    g.add_edge("tts", END)
    g.add_edge("translation", END)
    return g.compile()
