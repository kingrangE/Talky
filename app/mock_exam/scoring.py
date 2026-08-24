from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from app.mock_exam import SCORING_VERSION
from app.mock_exam.schemas import MockExamSet, QuestionType


def clamp(value: float, low: float = 0.0, high: float = 4.0) -> float:
    return max(low, min(high, value))


def audio_proxy_score(metrics: dict[str, Any], *, response_seconds: int) -> float:
    if not metrics.get("word_count"):
        return 0.0
    wpm = float(metrics.get("words_per_minute") or 0)
    pause_ratio = float(metrics.get("pause_ratio") or 1)
    probability = float(metrics.get("mean_word_probability") or 0)
    duration = float(metrics.get("duration_seconds") or 0)

    if 90 <= wpm <= 180:
        pace = 4.0
    elif 65 <= wpm < 90 or 180 < wpm <= 210:
        pace = 3.0
    elif 40 <= wpm < 65 or 210 < wpm <= 240:
        pace = 2.0
    else:
        pace = 1.0
    pause = clamp(4.5 - pause_ratio * 7)
    intelligibility = clamp((probability - 0.35) * 7)
    utilization = clamp((duration / max(1, response_seconds)) * 5)
    return round(0.30 * pace + 0.25 * pause + 0.30 * intelligibility + 0.15 * utilization, 2)


def read_aloud_completeness(reference: str, transcript: str) -> float:
    normalize = lambda value: " ".join(
        "".join(ch.lower() if ch.isalnum() else " " for ch in value).split()
    )
    return round(4 * SequenceMatcher(None, normalize(reference), normalize(transcript)).ratio(), 2)


WEIGHTS = {
    QuestionType.READ_ALOUD: (0.75, 0.25, 0.0),
    QuestionType.DESCRIBE_PICTURE: (0.35, 0.40, 0.25),
    QuestionType.RESPOND_QUESTION: (0.30, 0.30, 0.40),
    QuestionType.PROVIDED_INFORMATION: (0.25, 0.25, 0.50),
    QuestionType.EXPRESS_OPINION: (0.25, 0.40, 0.35),
}


LEVELS = [
    (190, "Level 8 (190–200)"),
    (160, "Level 7 (160–180)"),
    (130, "Level 6 (130–150)"),
    (110, "Level 5 (110–120)"),
    (80, "Level 4 (80–100)"),
    (60, "Level 3 (60–70)"),
    (40, "Level 2 (40–50)"),
    (0, "Level 1 (0–30)"),
]


def expected_level(score: int) -> str:
    return next(label for threshold, label in LEVELS if score >= threshold)


def aggregate_report(
    exam: MockExamSet,
    responses: list[dict[str, Any]],
    language_items: dict[int, dict[str, Any]],
    task_items: dict[int, dict[str, Any]],
    *,
    scoring_profile: str,
    disagreement: bool,
) -> dict[str, Any]:
    by_number = {int(item["question_number"]): item for item in responses}
    item_results: list[dict[str, Any]] = []
    scored_values: list[float] = []
    missing = 0
    for question in exam.questions:
        response = by_number.get(question.number)
        if response is None or response.get("status") in {"failed", "technical_error", "processing", "queued"}:
            missing += 1
            item_results.append({
                "question_number": question.number,
                "question_type": question.question_type.value,
                "status": "missing",
                "score": None,
                "evidence": [],
            })
            continue
        if response.get("status") == "no_response":
            scored_values.append(0.0)
            item_results.append({
                "question_number": question.number,
                "question_type": question.question_type.value,
                "status": "no_response",
                "score": 0.0,
                "evidence": ["No valid speech was detected."],
            })
            continue

        metrics = response.get("audio_metrics") or {}
        audio = audio_proxy_score(metrics, response_seconds=question.response_seconds)
        language = float((language_items.get(question.number) or {}).get("score", 2.0))
        task = float((task_items.get(question.number) or {}).get("score", 2.0))
        if question.question_type == QuestionType.READ_ALOUD:
            task = read_aloud_completeness(question.prompt, response.get("transcript", ""))
        wa, wl, wt = WEIGHTS[question.question_type]
        score = round(clamp(audio * wa + language * wl + task * wt), 2)
        scored_values.append(score)
        evidence = list((language_items.get(question.number) or {}).get("evidence", []))
        evidence += list((task_items.get(question.number) or {}).get("evidence", []))
        item_results.append({
            "question_number": question.number,
            "question_type": question.question_type.value,
            "status": "scored",
            "score": score,
            "audio_proxy": audio,
            "language_score": language,
            "task_score": task,
            "transcript": response.get("transcript", ""),
            "evidence": evidence[:4],
        })

    mean = sum(scored_values) / len(scored_values) if scored_values else 0.0
    center = int(round((mean / 4 * 200) / 10) * 10)
    base_width = 20 if scoring_profile == "advanced" else 30
    width = base_width + (10 if disagreement else 0) + (10 if missing else 0)
    low = max(0, int((center - width) // 10 * 10))
    high = min(200, int((center + width + 9) // 10 * 10))
    confidence = "medium" if scoring_profile == "advanced" and not disagreement and not missing else "low"
    return {
        "scoring_version": SCORING_VERSION,
        "beta": True,
        "score_low": low,
        "score_high": high,
        "expected_level": expected_level(center),
        "confidence": confidence,
        "scoring_profile": scoring_profile,
        "disagreement": disagreement,
        "missing_questions": missing,
        "items": item_results,
        "method_note": (
            "Local acoustic proxies and bounded LLM rubric agents were combined by a "
            "versioned deterministic formula. This is not an official score."
        ),
    }
