"""별점 누적 시 system prompt 새 버전을 생성하는 메타-LLM 그래프.

START → should_evolve → fetch → meta_compose → validate → insert → END
       └─[no]→ END                          └─[invalid]→ END

`run_evolve_if_needed(threshold)` 가 advisory lock 으로 동시성 보호하며 트리거한다.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.db.postgres import get_engine
from app.db.repo import (
    count_unconsumed_ratings,
    get_active_prompt,
    get_unconsumed_rated_reports,
    insert_prompt_version,
    mark_unconsumed_ratings_consumed,
)
from app.llm.factory import get_structured_llm
from app.settings import get_settings

log = logging.getLogger(__name__)

EVOLVE_LOCK_KEY = 73457301
REQUIRED_KEYWORDS = ("English", "영어", "tutor", "튜터", "conversation", "대화")
MIN_LEN, MAX_LEN = 200, 3000


class EvolveState(TypedDict, total=False):
    threshold: int
    proceed: bool
    high_rated: list[dict[str, Any]]
    low_rated: list[dict[str, Any]]
    rating_distribution: dict[int, int]
    current_prompt: dict[str, Any]
    new_prompt_content: str
    rationale: str
    diff_summary: list[str]
    new_prompt_id: str | None


class EvolveOutput(BaseModel):
    new_system_prompt: str = Field(description="새 system prompt 본문")
    rationale: str = Field(description="왜 이렇게 바꿨는지 한국어 2~3문장")
    diff_summary: list[str] = Field(
        default_factory=list,
        description="이전 대비 핵심 변경 항목 (예: '+ 더 짧고 친근한 톤')",
    )


META_PROMPT = """
당신은 영어 회화 학습 AI 튜터의 system prompt 를 더 나은 버전으로 진화시키는 메타-LLM 입니다.

입력으로 다음을 받습니다:
- current_system_prompt: 현재 활성 system prompt
- high_rated_conversations: 별점 높았던 대화의 요약/잘한 점/개선점
- low_rated_conversations: 별점 낮았던 대화의 요약/잘한 점/개선점
- rating_distribution: 1~5 별점 분포

수행할 일:
1. 별점 높은 대화의 패턴을 강화하고, 별점 낮은 대화의 문제를 해결하도록 system prompt 를 다듬어 새 버전을 작성합니다.
2. 한국어/영어 이중언어 회화 튜터라는 핵심 역할은 유지하세요.
3. 마크다운 본문은 plain text 로, 제목 헤더 (#) 는 사용하지 마세요.
4. 길이는 한국어/영어 합쳐 200~3000자.

JSON 으로 출력:
- new_system_prompt: 새 system prompt 본문
- rationale: 왜 이렇게 바꿨는지 한국어 2~3문장
- diff_summary: 이전 대비 핵심 변경 1~5개 (예: "+ 더 짧고 친근한 톤", "- 문법 교정 빈도 줄임")
"""


def should_evolve_node(state: EvolveState) -> dict:
    threshold = state.get("threshold") or get_settings().EVOLVE_BATCH
    count, dist = count_unconsumed_ratings()
    return {
        "proceed": count >= threshold,
        "rating_distribution": dist,
    }


def fetch_node(state: EvolveState) -> dict:
    if not state.get("proceed"):
        return {"proceed": False}
    high = get_unconsumed_rated_reports(top_n=3, order="desc")
    low = get_unconsumed_rated_reports(top_n=3, order="asc")
    current = get_active_prompt()
    if current is None:
        return {"proceed": False}
    return {
        "high_rated": high,
        "low_rated": low,
        "current_prompt": current,
    }


def meta_compose_node(state: EvolveState) -> dict:
    if not state.get("proceed"):
        return {"proceed": False}
    payload = {
        "current_system_prompt": state["current_prompt"]["content"],
        "high_rated_conversations": state.get("high_rated") or [],
        "low_rated_conversations": state.get("low_rated") or [],
        "rating_distribution": state.get("rating_distribution") or {},
    }
    llm = get_structured_llm(EvolveOutput, temperature=0.4)
    out: EvolveOutput = llm.invoke(
        [
            SystemMessage(content=META_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ]
    )
    return {
        "new_prompt_content": out.new_system_prompt,
        "rationale": out.rationale,
        "diff_summary": out.diff_summary,
    }


def validate_node(state: EvolveState) -> dict:
    if not state.get("proceed"):
        return {"proceed": False}
    content = (state.get("new_prompt_content") or "").strip()
    if not (MIN_LEN <= len(content) <= MAX_LEN):
        log.warning("evolve rejected: length %d out of [%d,%d]", len(content), MIN_LEN, MAX_LEN)
        return {"proceed": False}
    if not any(kw in content for kw in REQUIRED_KEYWORDS):
        log.warning("evolve rejected: missing required role keyword")
        return {"proceed": False}
    return {"proceed": True}


def insert_node(state: EvolveState) -> dict:
    if not state.get("proceed"):
        return {"proceed": False}
    new_id = insert_prompt_version(
        content=state["new_prompt_content"],
        rationale=state.get("rationale"),
        parent_id=state["current_prompt"]["id"],
        diff_summary=state.get("diff_summary") or [],
        activate=True,
    )
    mark_unconsumed_ratings_consumed()
    return {"new_prompt_id": new_id}


def _route_proceed(state: EvolveState) -> str:
    return "go" if state.get("proceed") else "stop"


@lru_cache(maxsize=1)
def build_evolve_graph():
    g = StateGraph(EvolveState)
    g.add_node("should_evolve", should_evolve_node)
    g.add_node("fetch", fetch_node)
    g.add_node("compose", meta_compose_node)
    g.add_node("validate", validate_node)
    g.add_node("insert", insert_node)

    g.add_edge(START, "should_evolve")
    g.add_conditional_edges(
        "should_evolve", _route_proceed, {"go": "fetch", "stop": END}
    )
    g.add_edge("fetch", "compose")
    g.add_edge("compose", "validate")
    g.add_conditional_edges(
        "validate", _route_proceed, {"go": "insert", "stop": END}
    )
    g.add_edge("insert", END)
    return g.compile()


def run_evolve_if_needed(threshold: int | None = None) -> str | None:
    """advisory lock 안에서 evolve graph 실행. 새 prompt id 또는 None."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            acquired = conn.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": EVOLVE_LOCK_KEY}
            ).scalar()
            if not acquired:
                return None
            try:
                graph = build_evolve_graph()
                state_in: EvolveState = (
                    {"threshold": threshold} if threshold is not None else {}
                )
                out = graph.invoke(state_in)
                return out.get("new_prompt_id")
            finally:
                conn.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": EVOLVE_LOCK_KEY}
                )
                conn.commit()
    except Exception as exc:
        log.warning("run_evolve_if_needed failed: %s", exc)
        return None
