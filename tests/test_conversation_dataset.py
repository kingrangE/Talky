import json
from collections import Counter
from pathlib import Path

import pytest

from app.llm.prompt_registry import CATALOG, builtin_text


DATA = Path(__file__).resolve().parents[1] / "data" / "test"


def read(path):
    return json.loads((DATA / path).read_text(encoding="utf-8"))


def test_manifest_inventory_and_judge_registration():
    manifest = read("manifest.json")
    assert manifest["version"] == "2.0"
    assert manifest["split"] == "development"
    assert manifest["synthetic"] is True
    assert set(manifest["suites"]) == {"memory", "dialogue", "expression"}
    rubrics = read(manifest["rubrics_file"])
    files = {"manifest.json", manifest["rubrics_file"], manifest["calibration_file"]}
    identifiers = []
    for suite_name, suite in manifest["suites"].items():
        cases = [case for file in suite["dataset_files"] for case in read(file)[suite["case_key"]]]
        assert len(cases) == suite["case_count"]
        identifiers.extend(case["id"] for case in cases)
        files.update(suite["dataset_files"])
        files.update(suite["source_files"])
        assert rubrics["suites"][suite_name]["judge"] == suite["judge_name"]
        prompt = (DATA / suite["judge_file"]).resolve()
        assert CATALOG[suite["judge_name"]] == prompt.name
        assert builtin_text(suite["judge_name"]) == prompt.read_text(encoding="utf-8")
    assert len(identifiers) == len(set(identifiers)) == 162
    assert files == {str(path.relative_to(DATA)) for path in DATA.rglob("*.json")}
    for suite in rubrics["suites"].values():
        for metric in suite["metrics"].values():
            assert set(metric["anchors"]) == {str(value) for value in metric["values"]}
            assert all(metric["anchors"].values())


def test_dialogue_scenarios_are_scoped_and_have_checkpoints():
    dataset = read("dialogue/scenarios.json")
    sources = {read(path)["set_id"]: read(path) for path in read("manifest.json")["suites"]["dialogue"]["source_files"]}
    metric_names = set(read("rubrics.json")["suites"]["dialogue"]["metrics"])
    focus = Counter()
    users = Counter()
    turn_ids = []
    assert dataset["reference_status"] == "draft_pending_human_review"
    for case in dataset["cases"]:
        assert dataset["source_version"] == sources[case["source_set_id"]]["version"]
        assert case["delivery"] == "scripted_user_turns"
        assert case["focus"] in case["applicable_metrics"]
        assert len(case["applicable_metrics"]) == len(set(case["applicable_metrics"]))
        assert set(case["applicable_metrics"]) <= metric_names
        assert len(case["turns"]) == 6
        assert Counter(turn["language"] for turn in case["turns"]) == {"ko": 3, "en": 3}
        for index, turn in enumerate(case["turns"], 1):
            assert turn["id"] == f"{case['id']}-u{index:02d}"
            assert turn["user_text"].strip()
            assert turn["expected_output"]["required_behaviors"]
            assert turn["expected_output"]["forbidden_behaviors"]
            assert "assistant" not in turn and "reference_answer" not in turn
            turn_ids.append(turn["id"])
        focus[case["focus"]] += 1
        users[case["source_set_id"]] += 1
    assert focus == {metric: 2 for metric in metric_names}
    assert users == {source: 4 for source in sources}
    assert len(turn_ids) == len(set(turn_ids)) == 72


@pytest.mark.parametrize("file,task,count", [
    ("language/ko_to_en.json", "ko_to_en", 24),
    ("language/en_correction.json", "en_correction", 36),
])
def test_expression_cases_have_meaning_and_edit_constraints(file, task, count):
    dataset = read(file)
    assert dataset["reference_status"] == "draft_pending_human_review"
    assert len(dataset["cases"]) == count
    for case in dataset["cases"]:
        inputs = case["input"]
        expected = case["expected_output"]
        assert inputs["task"] == task
        for field in ("user_text", "context", "register"):
            assert inputs[field].strip()
        assert type(case["metadata"]["context_required"]) is bool
        for field in ("meaning_units", "acceptable_rewrites", "forbidden_changes"):
            assert expected[field] and all(isinstance(value, str) and value.strip() for value in expected[field])
        assert len(expected["acceptable_rewrites"]) == len(set(expected["acceptable_rewrites"]))
        if case["metadata"]["category"] == "en_no_change":
            assert expected["expected_action"] == "keep"
            assert inputs["user_text"] in expected["acceptable_rewrites"]
        else:
            assert expected["expected_action"] == "rewrite"
            assert len(expected["acceptable_rewrites"]) >= 2
        if task == "ko_to_en":
            assert expected["explanation_requirements"] == []
        else:
            assert expected["explanation_requirements"]
    categories = Counter(case["metadata"]["category"] for case in dataset["cases"])
    assert categories == ({"ko_direct": 12, "ko_context": 12} if task == "ko_to_en" else {"en_grammar": 12, "en_fluency": 12, "en_no_change": 12})


def test_calibration_examples_reference_cases_and_valid_rubric_scores():
    manifest = read("manifest.json")
    rubrics = read(manifest["rubrics_file"])
    calibration = read(manifest["calibration_file"])
    assert calibration["review_status"] == "draft_pending_human_review"
    assert len(calibration["cases"]) == 12
    assert len({example["id"] for example in calibration["cases"]}) == 12
    assert Counter(example["suite"] for example in calibration["cases"]) == {"memory": 4, "dialogue": 4, "expression": 4}
    for example in calibration["cases"]:
        suite = manifest["suites"][example["suite"]]
        cases = {case["id"]: case for file in suite["dataset_files"] for case in read(file)[suite["case_key"]]}
        case = cases[example["case_id"]]
        assert example["candidate_output"] and example["reason"]
        for metric, score in example["expected_scores"].items():
            definition = rubrics["suites"][example["suite"]]["metrics"][metric]
            if score is None:
                assert definition["null_when"] is not None
            else:
                assert type(score) is int and score in definition["values"]
        if example["suite"] == "dialogue":
            assert 1 <= example["after_turn"] <= len(case["turns"])
            assert set(example["expected_scores"]) <= set(case["applicable_metrics"])
        elif example["suite"] == "memory":
            set_id = case["id"].split("-")[0]
            source = next(read(path) for path in suite["source_files"] if read(path)["set_id"] == set_id)
            messages = {message["id"] for session in source["sessions"] for message in session["messages"]}
            assert set(example["model_visible_message_ids"]) <= messages
