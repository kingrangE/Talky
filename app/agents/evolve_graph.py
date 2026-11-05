"""사용자별 별점 누적 시 해당 사용자의 system prompt 새 버전을 생성하는 메타-LLM 그래프.

START → should_evolve → fetch → meta_compose → validate → insert → END
       └─[no]→ END                          └─[invalid]→ END

`run_evolve_if_needed(user_id, threshold)` 가 advisory lock 으로 동시성 보호하며 트리거한다.
"""

from __future__ import annotations

import json
import logging
import uuid
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
    rollback_active_prompt_if_degraded,
)
from app.llm.factory import get_structured_llm
from app.settings import get_settings
from app.runtime import bounded_messages

log = logging.getLogger(__name__)

REQUIRED_KEYWORDS = ("English", "영어", "tutor", "튜터", "conversation", "대화")
MIN_LEN, MAX_LEN = 200, 3000


class EvolveState(TypedDict, total=False):
    user_id: str
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
    rating_snapshot: list[dict[str, Any]]


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
1. 입력은 한 사용자의 평가입니다. 그 사용자의 선호와 학습 필요에 맞춰 별점 높은 대화의 패턴을 강화하고, 별점 낮은 대화의 문제를 해결하도록 system prompt 를 다듬어 새 버전을 작성합니다.
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
    current = get_active_prompt(state["user_id"])
    if current is None:
        return {"proceed": False, "rating_distribution": {}}
    count, dist = count_unconsumed_ratings(state["user_id"], current["id"])
    return {
        "proceed": count >= threshold,
        "rating_distribution": dist,
        "current_prompt": current,
    }


def fetch_node(state: EvolveState) -> dict:
    if not state.get("proceed"):
        return {}
    current = state.get("current_prompt")
    if not current:
        return {"proceed": False}
    rows = get_unconsumed_rated_reports(
        user_id=state["user_id"], top_n=None, order="desc", prompt_version_id=current["id"]
    )
    distribution = {stars: sum(row["stars"] == stars for row in rows) for stars in range(1, 6)}
    return {
        "proceed": len(rows) >= (state.get("threshold") or get_settings().EVOLVE_BATCH),
        "high_rated": rows[:3],
        "low_rated": list(reversed(rows[-3:])),
        "rating_distribution": distribution,
        "rating_snapshot": [{"id": row["id"], "revision": row["revision"]} for row in rows],
        "current_prompt": current,
    }


def meta_compose_node(state: EvolveState) -> dict:
    if not state.get("proceed"):
        return {}
    payload = {
        "current_system_prompt": state["current_prompt"]["content"],
        "high_rated_conversations": state.get("high_rated") or [],
        "low_rated_conversations": state.get("low_rated") or [],
        "rating_distribution": state.get("rating_distribution") or {},
    }
    llm = get_structured_llm(EvolveOutput, temperature=0.4)
    out: EvolveOutput = llm.invoke(bounded_messages(
        [
            SystemMessage(content=META_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ]
    ))
    return {
        "new_prompt_content": out.new_system_prompt,
        "rationale": out.rationale,
        "diff_summary": out.diff_summary,
    }


def validate_node(state: EvolveState) -> dict:
    if not state.get("proceed"):
        return {}
    content = (state.get("new_prompt_content") or "").strip()
    if not (MIN_LEN <= len(content) <= MAX_LEN):
        log.warning("evolve rejected: length %d out of [%d,%d]", len(content), MIN_LEN, MAX_LEN)
        return {"proceed": False}
    if not any(kw in content for kw in REQUIRED_KEYWORDS):
        log.warning("evolve rejected: missing required role keyword")
        return {"proceed": False}
    return {}


def insert_node(state: EvolveState) -> dict:
    if not state.get("proceed"):
        return {}
    new_id = insert_prompt_version(
        user_id=state["user_id"],
        content=state["new_prompt_content"],
        rationale=state.get("rationale"),
        parent_id=state["current_prompt"]["id"],
        diff_summary=state.get("diff_summary") or [],
        activate=True,
        rating_snapshot=state["rating_snapshot"],
    )
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


def run_evolve_if_needed(user_id: str, threshold: int | None = None) -> dict[str, str] | None:
    """품질 하락 롤백 후 필요하면 evolve graph를 실행한다."""
    # UUID의 하위 63비트로 사용자별 잠금을 잡는다.
    user_id = str(uuid.UUID(str(user_id)))
    lock_key = uuid.UUID(user_id).int & ((1 << 63) - 1)
    try:
        engine = get_engine()
        with engine.connect() as conn:
            acquired = conn.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": lock_key}
            ).scalar()
            if not acquired:
                return None
            try:
                cfg = get_settings()
                effective_threshold = threshold or cfg.EVOLVE_BATCH
                rollback_id = rollback_active_prompt_if_degraded(
                    user_id=user_id,
                    min_samples=effective_threshold,
                    drop_threshold=cfg.ROLLBACK_THRESHOLD,
                )
                if rollback_id:
                    return {"action": "rolled_back", "prompt_id": rollback_id}
                graph = build_evolve_graph()
                state_in: EvolveState = {"user_id": user_id}
                if threshold is not None:
                    state_in["threshold"] = threshold
                out = graph.invoke(state_in)
                new_id = out.get("new_prompt_id")
                if new_id:
                    return {"action": "evolved", "prompt_id": new_id}
                return None
            finally:
                conn.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key}
                )
                conn.commit()
    except Exception as exc:
        log.warning("run_evolve_if_needed failed: %s", exc)
        return None
