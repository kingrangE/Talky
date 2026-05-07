"""대화 종료 시 분석 보고서 + Topic 적재 그래프.

START → load_history → summarize → persist_report → ingest_topics → END
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.db.repo import load_messages, mark_conversation_ended, save_report
from app.graph_db.ingest import ingest_topics
from app.llm.factory import get_chat_llm


class EndingState(TypedDict, total=False):
    conversation_id: str
    user_id: str
    history: list[dict[str, Any]]
    report: dict[str, Any]


class ReportPayload(BaseModel):
    summary: str = Field(description="한국어 2~4문장 요약")
    strengths: list[str] = Field(default_factory=list, description="잘한 점 1~3개 (한국어)")
    weaknesses: list[str] = Field(default_factory=list, description="개선이 필요한 점 1~3개 (한국어)")
    vocab_learned: list[str] = Field(default_factory=list, description="등장한 영어 단어/표현 5~10개")
    topics: list[str] = Field(default_factory=list, description="1~5 짧은 영어 토픽 단어/구")


SUMMARIZE_PROMPT = """
다음은 한국어/영어 회화 학습 사용자와 AI 튜터의 대화 기록입니다. 이번 세션의 학습 분석 보고서를 JSON 으로 작성하세요.

스키마:
- summary: 한국어 2~4문장 요약
- strengths: 잘한 점 1~3개 (한국어 짧은 항목)
- weaknesses: 개선이 필요한 점 1~3개 (한국어 짧은 항목)
- vocab_learned: 등장한 영어 단어/표현 5~10개 (영어, 짧게)
- topics: 1~5 짧은 영어 토픽 단어/구 (소문자, 단수형)
"""


def load_history_node(state: EndingState) -> dict:
    cid = state["conversation_id"]
    rows = load_messages(cid)
    history = [{"role": r["role"], "content": r["content"]} for r in rows]
    return {"history": history}


def summarize_node(state: EndingState) -> dict:
    history = state.get("history") or []
    if not history:
        return {
            "report": {
                "summary": "대화 기록이 없습니다.",
                "strengths": [],
                "weaknesses": [],
                "vocab_learned": [],
                "topics": [],
            }
        }

    history_text = "\n".join(f"[{m['role']}] {m['content']}" for m in history)
    llm = get_chat_llm(temperature=0.3).with_structured_output(ReportPayload)
    out: ReportPayload = llm.invoke(
        [
            SystemMessage(content=SUMMARIZE_PROMPT),
            HumanMessage(content=history_text),
        ]
    )
    return {"report": out.model_dump()}


def persist_report_node(state: EndingState) -> dict:
    cid = state["conversation_id"]
    report = state.get("report") or {}
    save_report(
        conversation_id=cid,
        summary=report.get("summary", ""),
        strengths=report.get("strengths") or [],
        weaknesses=report.get("weaknesses") or [],
        vocab_learned=report.get("vocab_learned") or [],
    )
    mark_conversation_ended(cid)
    return {}


def ingest_topics_node(state: EndingState) -> dict:
    cid = state["conversation_id"]
    topics = (state.get("report") or {}).get("topics") or []
    if topics:
        ingest_topics(conversation_id=cid, topics=topics)
    return {}


@lru_cache(maxsize=1)
def build_ending_graph():
    g = StateGraph(EndingState)
    g.add_node("load_history", load_history_node)
    g.add_node("summarize", summarize_node)
    g.add_node("persist_report", persist_report_node)
    g.add_node("ingest_topics", ingest_topics_node)

    g.add_edge(START, "load_history")
    g.add_edge("load_history", "summarize")
    g.add_edge("summarize", "persist_report")
    g.add_edge("persist_report", "ingest_topics")
    g.add_edge("ingest_topics", END)
    return g.compile()
