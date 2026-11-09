import json
from collections import Counter
from pathlib import Path

import pytest


DATA = Path(__file__).resolve().parents[1] / "data" / "test"
SETS = ("s1_daily_life", "s2_work_projects", "s3_travel_plans")
QUESTION_CATEGORIES = ("single_hop", "multi_hop", "hallucination")


def read(category, name):
    return json.loads((DATA / category / f"{name}.json").read_text(encoding="utf-8"))


def test_dataset_inventory_and_global_identifiers():
    for category in ("conversations", *QUESTION_CATEGORIES):
        assert {p.stem for p in (DATA / category).glob("*.json")} == set(SETS)
    for field, category, extract in (
        ("fixture_user_id", "conversations", lambda d: [d["fixture_user_id"]]),
        ("session_id", "conversations", lambda d: [s["id"] for s in d["sessions"]]),
        ("message_id", "conversations", lambda d: [m["id"] for s in d["sessions"] for m in s["messages"]]),
        ("question_id", None, lambda d: [q["id"] for q in d["questions"]]),
        ("question", None, lambda d: [q["question"].strip().casefold() for q in d["questions"]]),
    ):
        categories = [category] if category else QUESTION_CATEGORIES
        values = [value for c in categories for name in SETS for value in extract(read(c, name))]
        assert len(values) == len(set(values)), field


@pytest.mark.parametrize("name", SETS)
def test_dataset_sources_and_question_evidence(name):
    conversation = read("conversations", name)
    set_id = name.split("_")[0]
    assert conversation["set_id"] == set_id
    assert conversation["synthetic"] is True
    assert len(conversation["sessions"]) == 3
    messages = {}
    for session in conversation["sessions"]:
        assert len(session["messages"]) == 16
        assert [m["role"] for m in session["messages"]] == ["user", "assistant"] * 8
        for message in session["messages"]:
            assert message["content"].strip()
            assert message["id"].startswith(f"{set_id}-m")
            assert message["language"] in {"ko", "en"}
            messages[message["id"]] = message
    assert Counter(m["language"] for m in messages.values() if m["role"] == "user") == {"ko": 12, "en": 12}
    assert all(m["language"] == "en" for m in messages.values() if m["role"] == "assistant")

    for category, prefix, hop_counts in (
        ("single_hop", "sh", {1: 10}),
        ("multi_hop", "mh", {2: 5, 3: 5}),
    ):
        dataset = read(category, name)
        assert dataset["version"] == conversation["version"] == "1.2"
        assert dataset["set_id"] == set_id
        assert dataset["question_type"] == category
        questions = dataset["questions"]
        assert len(questions) == 10
        assert Counter(q["reasoning_hops"] for q in questions) == hop_counts
        assert Counter(q["language"] for q in questions) == {"ko": 5, "en": 5}
        for index, question in enumerate(questions, 1):
            assert question["id"] == f"{set_id}-{prefix}{index:02d}"
            assert question["question"].strip()
            assert question["reference_answer"].strip()
            assert question["expected_facts"]
            for fact in question["expected_facts"]:
                assert set(fact) == {"ko", "en"}
                assert all(isinstance(value, str) and value.strip() for value in fact.values())
            evidence = question["evidence"]
            evidence_ids = [item["message_id"] for item in evidence]
            assert len(evidence_ids) == len(set(evidence_ids)) == question["reasoning_hops"]
            for item in evidence:
                source = messages[item["message_id"]]
                assert source["role"] == "user"
                assert item["quote"] == source["content"]
                assert item["language"] == source["language"]
            assert evidence_ids == sorted(evidence_ids)


def test_korean_sources_are_evaluated_in_both_question_types():
    single_languages = Counter()
    for name in SETS:
        single = read("single_hop", name)["questions"]
        assert {q["evidence"][0]["language"] for q in single} == {"ko", "en"}
        single_languages.update(q["evidence"][0]["language"] for q in single)
        for category in ("single_hop", "multi_hop"):
            questions = read(category, name)["questions"]
            # Both question languages must exercise facts originally stated in Korean.
            assert {q["language"] for q in questions if any(e["language"] == "ko" for e in q["evidence"])} == {"ko", "en"}
        for question in read("multi_hop", name)["questions"]:
            assert any(e["language"] == "ko" for e in question["evidence"])
    assert single_languages == {"ko": 15, "en": 15}


def test_hallucination_questions_and_source_review():
    scenarios = Counter()
    scenario_languages = Counter()
    for name in SETS:
        source = read("conversations", name)
        messages = {m["id"]: m for s in source["sessions"] for m in s["messages"]}
        dataset = read("hallucination", name)
        assert dataset["version"] == "1.0"
        assert dataset["source_version"] == source["version"]
        assert dataset["set_id"] == source["set_id"]
        assert dataset["question_type"] == "hallucination"
        assert dataset["review_scope"] == "all_sessions"
        questions = dataset["questions"]
        assert len(questions) == 10
        assert Counter(q["language"] for q in questions) == {"ko": 5, "en": 5}
        for index, question in enumerate(questions, 1):
            assert question["id"] == f"{source['set_id']}-ha{index:02d}"
            assert question["expected_behavior"] == "clarify"
            assert question["reasoning_hops"] == 0
            assert question["expected_facts"] == []
            assert question["evidence"] == []
            for field in ("question", "reference_answer", "review_note"):
                assert isinstance(question[field], str) and question[field].strip()
            related = question["related_message_ids"]
            assert len(related) == len(set(related))
            assert related == sorted(related)
            assert all(messages[mid]["role"] == "user" for mid in related)
            if question["scenario_type"] == "ambiguous_reference":
                assert len(related) >= 2
            scenarios[question["scenario_type"]] += 1
            scenario_languages[question["scenario_type"], question["language"]] += 1
    assert scenarios == {"missing_information": 10, "ambiguous_reference": 10, "false_premise": 10}
    assert scenario_languages == {(scenario, language): 5 for scenario in scenarios for language in ("ko", "en")}
