from app.mock_exam.content import list_approved_sets
from app.mock_exam.scoring import aggregate_report, expected_level, read_aloud_completeness


def test_read_aloud_completeness_is_normalized():
    assert read_aloud_completeness("Hello, WORLD!", "hello world") == 4.0


def test_score_report_widens_when_agents_disagree():
    exam = list_approved_sets()[0]
    responses = [
        {
            "question_number": question.number,
            "status": "scored",
            "transcript": question.prompt,
            "audio_metrics": {
                "word_count": 20,
                "words_per_minute": 120,
                "pause_ratio": 0.2,
                "mean_word_probability": 0.85,
                "duration_seconds": question.response_seconds * 0.8,
            },
        }
        for question in exam.questions
    ]
    items = {number: {"score": 3.0, "evidence": ["supported"]} for number in range(1, 12)}
    stable = aggregate_report(
        exam, responses, items, items, scoring_profile="advanced", disagreement=False
    )
    disputed = aggregate_report(
        exam, responses, items, items, scoring_profile="advanced", disagreement=True
    )
    assert disputed["score_high"] - disputed["score_low"] > stable["score_high"] - stable["score_low"]
    assert disputed["confidence"] == "low"


def test_level_labels_cover_score_boundaries():
    assert expected_level(200).startswith("Level 8")
    assert expected_level(0).startswith("Level 1")
