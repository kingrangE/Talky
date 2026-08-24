from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.llm.factory import get_structured_llm
from app.mock_exam.content import load_exam_set
from app.mock_exam.scoring import aggregate_report


class EvaluationItem(BaseModel):
    question_number: int = Field(ge=1, le=11)
    score: float = Field(ge=0, le=4)
    evidence: list[str] = Field(default_factory=list, max_length=4)
    strength: str = ""
    improvement: str = ""
    minimal_revision: str = ""
    band_example: str = ""


class EvaluationBatch(BaseModel):
    items: list[EvaluationItem]
    audit_summary: str


class EvidenceDecision(BaseModel):
    disagreement_resolved: bool
    revised_language_items: list[EvaluationItem] = Field(default_factory=list)
    revised_task_items: list[EvaluationItem] = Field(default_factory=list)
    rationale: str


class ScoringState(TypedDict, total=False):
    session_id: str
    exam_set_id: str
    scoring_profile: str
    responses: list[dict[str, Any]]
    language_items: dict[int, dict[str, Any]]
    task_items: dict[int, dict[str, Any]]
    disagreement: bool
    evidence_audit: dict[str, Any]
    report: dict[str, Any]


LANGUAGE_PROMPT = """You are the Language Quality Agent in a bounded speaking assessment workflow.
Evaluate only grammar, vocabulary, cohesion, and clarity supported by each transcript. Do not infer
pronunciation from text. Return one 0-4 item for every question. Quote short transcript evidence.
For questions 1-2, score text fidelity and clarity only. Write strength and improvement in Korean,
while minimal_revision and band_example remain English. Never claim this is an official score."""

TASK_PROMPT = """You are the Task Fulfillment Agent in a bounded speaking assessment workflow.
Compare each transcript with the question, rubric anchors, expected facts, and visible information.
Score relevance and completeness from 0-4. Do not judge accent or pronunciation. Quote short evidence.
For read-aloud questions, return score 2 because deterministic text alignment will replace it.
Write explanations in Korean and examples in English. Never claim this is an official score."""

EVIDENCE_PROMPT = """You are the Evidence Verification Agent. Review only items where the language
and task scores differ by at least 2 points. Check whether the cited transcript supports each score.
You may revise scores once. Return structured revised items and a concise Korean rationale. Do not
produce hidden chain-of-thought or an overall score."""


def _agent_input(state: ScoringState) -> str:
    exam = load_exam_set(state["exam_set_id"])
    response_map = {r["question_number"]: r for r in state["responses"]}
    payload = []
    for q in exam.questions:
        response = response_map.get(q.number, {})
        payload.append({
            "question_number": q.number,
            "type": q.question_type.value,
            "prompt": q.prompt,
            "rubric_anchors": q.rubric_anchors,
            "expected_facts": q.expected_facts,
            "transcript": response.get("transcript", ""),
            "status": response.get("status", "missing"),
        })
    return json.dumps(payload, ensure_ascii=False)


def _fallback_items(state: ScoringState, *, task: bool) -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    for response in state["responses"]:
        number = int(response["question_number"])
        words = len((response.get("transcript") or "").split())
        score = 0.0 if response.get("status") == "no_response" else min(4.0, 1.0 + words / 18)
        output[number] = {
            "question_number": number,
            "score": round(score, 2),
            "evidence": [f"Transcript contains {words} words."],
            "strength": "응답을 제한 시간 안에 제출했습니다." if words else "",
            "improvement": "핵심 답변 뒤에 구체적인 근거를 덧붙이세요.",
            "minimal_revision": response.get("transcript", ""),
            "band_example": "Add a clear answer, a reason, and one specific example.",
            "fallback": True,
            "agent": "task" if task else "language",
        }
    return output


def _complete_items(
    state: ScoringState, items: dict[int, dict[str, Any]], *, task: bool
) -> dict[int, dict[str, Any]]:
    """Make malformed or incomplete local-model batches deterministic and total."""
    fallbacks = _fallback_items(state, task=task)
    completed: dict[int, dict[str, Any]] = {}
    for number in range(1, 12):
        completed[number] = items.get(number) or fallbacks.get(number) or {
            "question_number": number,
            "score": 0.0,
            "evidence": ["No response record was available."],
            "strength": "",
            "improvement": "응답이 누락되지 않도록 마이크와 네트워크 상태를 확인하세요.",
            "minimal_revision": "",
            "band_example": "Give a direct answer and support it with one relevant detail.",
            "fallback": True,
            "agent": "task" if task else "language",
        }
    return completed


def language_agent_node(state: ScoringState) -> dict[str, Any]:
    try:
        llm = get_structured_llm(EvaluationBatch, temperature=0.1)
        result: EvaluationBatch = llm.invoke([
            SystemMessage(content=LANGUAGE_PROMPT), HumanMessage(content=_agent_input(state))
        ])
        items = {item.question_number: item.model_dump() for item in result.items}
        return {"language_items": _complete_items(state, items, task=False)}
    except Exception:
        return {"language_items": _complete_items(state, {}, task=False)}


def task_agent_node(state: ScoringState) -> dict[str, Any]:
    try:
        llm = get_structured_llm(EvaluationBatch, temperature=0.1)
        result: EvaluationBatch = llm.invoke([
            SystemMessage(content=TASK_PROMPT), HumanMessage(content=_agent_input(state))
        ])
        items = {item.question_number: item.model_dump() for item in result.items}
    except Exception:
        items = {}
    items = _complete_items(state, items, task=True)
    disagreement = any(
        abs(float(items.get(number, {}).get("score", 2)) - float(language.get("score", 2))) >= 2
        for number, language in state.get("language_items", {}).items()
    )
    return {"task_items": items, "disagreement": disagreement}


def route_evidence(state: ScoringState) -> str:
    return "verify" if state.get("disagreement") else "aggregate"


def evidence_agent_node(state: ScoringState) -> dict[str, Any]:
    disagreements = []
    for number, language in state.get("language_items", {}).items():
        task = state.get("task_items", {}).get(number, {})
        if abs(float(language.get("score", 2)) - float(task.get("score", 2))) >= 2:
            disagreements.append({"question_number": number, "language": language, "task": task})
    try:
        llm = get_structured_llm(EvidenceDecision, temperature=0)
        decision: EvidenceDecision = llm.invoke([
            SystemMessage(content=EVIDENCE_PROMPT),
            HumanMessage(content=json.dumps({
                "disagreements": disagreements,
                "assessment_input": json.loads(_agent_input(state)),
            }, ensure_ascii=False)),
        ])
        language = dict(state.get("language_items", {}))
        task = dict(state.get("task_items", {}))
        language.update({item.question_number: item.model_dump() for item in decision.revised_language_items})
        task.update({item.question_number: item.model_dump() for item in decision.revised_task_items})
        still_disagrees = any(
            abs(float(task.get(number, {}).get("score", 2)) - float(item.get("score", 2))) >= 2
            for number, item in language.items()
        )
        return {
            "language_items": language,
            "task_items": task,
            "disagreement": still_disagrees,
            "evidence_audit": decision.model_dump(),
        }
    except Exception as exc:
        return {"evidence_audit": {"error": type(exc).__name__, "reviewed_once": True}}


def aggregate_node(state: ScoringState) -> dict[str, Any]:
    exam = load_exam_set(state["exam_set_id"])
    report = aggregate_report(
        exam,
        state["responses"],
        state.get("language_items", {}),
        state.get("task_items", {}),
        scoring_profile=state["scoring_profile"],
        disagreement=bool(state.get("disagreement")),
    )
    report["agent_audit"] = {
        "language_agent": "completed",
        "task_agent": "completed",
        "evidence_agent": state.get("evidence_audit") or {"skipped": True},
    }
    improvements = []
    for number in range(1, 12):
        language = state.get("language_items", {}).get(number, {})
        task = state.get("task_items", {}).get(number, {})
        if language.get("improvement") or task.get("improvement"):
            improvements.append({
                "question_number": number,
                "advice": task.get("improvement") or language.get("improvement"),
                "minimal_revision": language.get("minimal_revision", ""),
                "band_example": task.get("band_example") or language.get("band_example", ""),
            })
    report["priority_improvements"] = improvements[:3]
    return {"report": report}


@lru_cache(maxsize=1)
def build_scoring_graph():
    graph = StateGraph(ScoringState)
    graph.add_node("language_agent", language_agent_node)
    graph.add_node("task_agent", task_agent_node)
    graph.add_node("evidence_agent", evidence_agent_node)
    graph.add_node("aggregate", aggregate_node)
    graph.add_edge(START, "language_agent")
    graph.add_edge("language_agent", "task_agent")
    graph.add_conditional_edges(
        "task_agent", route_evidence, {"verify": "evidence_agent", "aggregate": "aggregate"}
    )
    graph.add_edge("evidence_agent", "aggregate")
    graph.add_edge("aggregate", END)
    return graph.compile()
