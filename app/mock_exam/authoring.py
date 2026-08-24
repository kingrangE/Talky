from __future__ import annotations

import argparse
import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.llm.factory import get_local_structured_llm, get_structured_llm
from app.mock_exam.content import validate_exam_set
from app.mock_exam.schemas import MockExamSet
from app.settings import get_settings


class Blueprint(BaseModel):
    set_id: str
    title: str
    workplace_theme: str
    question_goals: list[str] = Field(min_length=11, max_length=11)
    picture_requirements: list[str] = Field(min_length=2, max_length=2)
    information_panel_theme: str
    opinion_theme: str


class CriticDecision(BaseModel):
    verdict: Literal["approve", "repair", "reject"]
    defects: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    repair_instructions: list[str] = Field(default_factory=list)


class AuthoringState(TypedDict, total=False):
    brief: str
    blueprint: dict[str, Any]
    candidate: dict[str, Any]
    deterministic_findings: list[dict[str, Any]]
    critic: dict[str, Any]
    attempts: int
    status: Literal["candidate", "approved", "rejected"]


BLUEPRINT_PROMPT = """You are the Blueprint Agent for an original workplace-English speaking mock.
Create an eleven-question plan matching these task counts: two read-aloud, two picture-description,
three short responses, three responses using one information panel, and one opinion response.
Do not copy or paraphrase published test questions. Use everyday workplace and community contexts."""

WRITER_PROMPT = """You are the Item Writer Agent. Produce a complete MockExamSet from the blueprint.
Use the exact timing and ordered task layout represented by the schema. All prompts must be original.
Images must be represented by asset manifests with a verified public license; never invent a license.
If no verified image source is available, leave a clear candidate manifest for human replacement.
Create deterministic table data for questions 8-10 and ensure every expected fact is present."""

RUBRIC_PROMPT = """You are the Rubric Builder Agent. Review the candidate and return a corrected
MockExamSet. Preserve original content when valid. Ensure each question has observable rubric anchors,
expected facts are supported by its image description or information panel, and no official score is
claimed. Keep status as candidate and do not approve your own work."""

CRITIC_PROMPT = """You are an independent Critic Agent. Inspect the candidate, deterministic findings,
timing, cross-question leakage, answerability, difficulty, asset provenance, and table consistency.
Do not rewrite content. Return only a structured approve/repair/reject decision with verifiable evidence."""


def blueprint_node(state: AuthoringState) -> dict[str, Any]:
    llm = get_structured_llm(Blueprint, temperature=0.5)
    out: Blueprint = llm.invoke([
        SystemMessage(content=BLUEPRINT_PROMPT), HumanMessage(content=state["brief"])
    ])
    return {"blueprint": out.model_dump(), "attempts": 0, "status": "candidate"}


def item_writer_node(state: AuthoringState) -> dict[str, Any]:
    llm = get_structured_llm(MockExamSet, temperature=0.4)
    request = {"blueprint": state["blueprint"], "repair": state.get("critic", {})}
    out: MockExamSet = llm.invoke([
        SystemMessage(content=WRITER_PROMPT),
        HumanMessage(content=json.dumps(request, ensure_ascii=False)),
    ])
    candidate = out.model_copy(update={"status": "candidate"})
    return {"candidate": candidate.model_dump(mode="json")}


def asset_matcher_node(state: AuthoringState) -> dict[str, Any]:
    candidate = MockExamSet.model_validate(state["candidate"])
    findings = validate_exam_set(candidate).model_dump(mode="json")["findings"]
    for asset in candidate.assets:
        if asset.kind == "image" and (
            not asset.source_url or not asset.license_url or not asset.author
        ):
            findings.append({
                "code": "unverified_asset",
                "severity": "error",
                "question_number": None,
                "message": f"{asset.asset_id} needs a human-verified public license manifest",
            })
    return {"deterministic_findings": findings}


def rubric_builder_node(state: AuthoringState) -> dict[str, Any]:
    llm = get_structured_llm(MockExamSet, temperature=0.1)
    out: MockExamSet = llm.invoke([
        SystemMessage(content=RUBRIC_PROMPT),
        HumanMessage(content=json.dumps(state["candidate"], ensure_ascii=False)),
    ])
    return {"candidate": out.model_copy(update={"status": "candidate"}).model_dump(mode="json")}


def critic_node(state: AuthoringState) -> dict[str, Any]:
    candidate = MockExamSet.model_validate(state["candidate"])
    findings = validate_exam_set(candidate).model_dump(mode="json")["findings"]
    for asset in candidate.assets:
        if asset.kind == "image" and (
            not asset.source_url or not asset.license_url or not asset.author
        ):
            findings.append({
                "code": "unverified_asset",
                "severity": "error",
                "question_number": None,
                "message": f"{asset.asset_id} needs a human-verified public license manifest",
            })
    cfg = get_settings()
    llm = get_local_structured_llm(
        CriticDecision, model_name=cfg.MOCK_EXAM_REVIEW_MODEL, temperature=0
    )
    out: CriticDecision = llm.invoke([
        SystemMessage(content=CRITIC_PROMPT),
        HumanMessage(content=json.dumps({
            "candidate": state["candidate"],
            "deterministic_findings": findings,
        }, ensure_ascii=False)),
    ])
    if any(item.get("severity") == "error" for item in findings):
        out.verdict = "repair" if state.get("attempts", 0) < 2 else "reject"
    return {"critic": out.model_dump(), "deterministic_findings": findings}


def adjudicator_node(state: AuthoringState) -> dict[str, Any]:
    verdict = state["critic"]["verdict"]
    attempts = int(state.get("attempts", 0))
    if verdict == "approve" and not state.get("deterministic_findings"):
        return {"status": "approved"}
    if verdict == "repair" and attempts < 2:
        return {"status": "candidate", "attempts": attempts + 1}
    return {"status": "rejected"}


def route_adjudication(state: AuthoringState) -> str:
    return {"approved": "approved", "rejected": "rejected"}.get(state["status"], "repair")


@lru_cache(maxsize=1)
def build_authoring_graph():
    graph = StateGraph(AuthoringState)
    graph.add_node("blueprint", blueprint_node)
    graph.add_node("item_writer", item_writer_node)
    graph.add_node("asset_matcher", asset_matcher_node)
    graph.add_node("rubric_builder", rubric_builder_node)
    graph.add_node("critic", critic_node)
    graph.add_node("adjudicator", adjudicator_node)
    graph.add_edge(START, "blueprint")
    graph.add_edge("blueprint", "item_writer")
    graph.add_edge("item_writer", "asset_matcher")
    graph.add_edge("asset_matcher", "rubric_builder")
    graph.add_edge("rubric_builder", "critic")
    graph.add_edge("critic", "adjudicator")
    graph.add_conditional_edges(
        "adjudicator",
        route_adjudication,
        {"repair": "item_writer", "approved": END, "rejected": END},
    )
    return graph.compile()


def write_pr_candidate(result: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate = dict(result["candidate"])
    candidate["status"] = "candidate"
    audit = candidate.setdefault("audit", {})
    audit.update({
        "attempts": result.get("attempts", 0),
        "deterministic_checks": [
            finding.get("code", "unknown") for finding in result.get("deterministic_findings", [])
        ],
        "reviewer_findings": result.get("critic", {}).get("defects", []),
        "adjudication": "rejected" if result.get("status") == "rejected" else "approved",
        "approved_by": "PENDING_GITHUB_REVIEW",
        "approved_at": str(date.today()),
    })
    path = output_dir / f"{candidate['id']}-{candidate['version']}.candidate.json"
    path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path = path.with_suffix(".validation.json")
    report_path.write_text(json.dumps({
        "status": result.get("status"),
        "attempts": result.get("attempts"),
        "deterministic_findings": result.get("deterministic_findings", []),
        "critic": result.get("critic", {}),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a PR-ready mock exam candidate")
    parser.add_argument("brief")
    parser.add_argument("--output", default="mock-exam-candidates")
    args = parser.parse_args()
    result = build_authoring_graph().invoke({"brief": args.brief})
    path = write_pr_candidate(result, Path(args.output))
    print(path)


if __name__ == "__main__":
    main()
