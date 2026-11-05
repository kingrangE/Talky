from types import SimpleNamespace

from app.graph_db import neo4j_client
from app.mock_exam import scoring, scoring_graph
from app.mock_exam.schemas import MockExamSet
from pathlib import Path


def test_zero_pause_ratio_does_not_reduce_audio_score():
    metrics = dict(word_count=20, words_per_minute=120,
                   mean_word_probability=0.85, duration_seconds=30)
    assert scoring.audio_proxy_score({**metrics, "pause_ratio": 0.0}, response_seconds=45) >= (
        scoring.audio_proxy_score({**metrics, "pause_ratio": 0.001}, response_seconds=45)
    )


def test_schema_initialization_retries_after_database_recovers(monkeypatch):
    calls = []

    class Session:
        def __enter__(self):
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionError("temporary outage")
            return self

        def __exit__(self, *_args):
            pass

        def run(self, _query):
            return SimpleNamespace(consume=lambda: None)

    neo4j_client._initialize_schema.cache_clear()
    monkeypatch.setattr(neo4j_client, "get_driver", lambda: SimpleNamespace(session=Session))
    try:
        assert neo4j_client.ensure_schema() is False
        assert neo4j_client.ensure_schema() is True
        assert neo4j_client.ensure_schema() is True
        assert len(calls) == 2
    finally:
        neo4j_client._initialize_schema.cache_clear()


def test_failed_model_assessment_is_not_reported_as_completed(monkeypatch):
    exam = MockExamSet.model_validate_json(
        Path("app/mock_exam/data/sets/workplace-basics-001.json").read_text()
    )
    monkeypatch.setattr(scoring_graph, "load_exam_set", lambda _id: exam)

    def unavailable(*_args, **_kwargs):
        raise ConnectionError("model unavailable")

    monkeypatch.setattr(scoring_graph, "get_structured_llm", unavailable)
    state = {
        "exam_set_id": exam.id, "scoring_profile": "advanced",
        "responses": [
            {"question_number": q.number, "status": "scored", "transcript": "one two three"}
            for q in exam.questions
        ],
    }
    state.update(scoring_graph.language_agent_node(state))
    state.update(scoring_graph.task_agent_node(state))
    report = scoring_graph.aggregate_node(state)["report"]
    assert report["agent_audit"]["language_agent"] == "fallback"
    assert report["agent_audit"]["task_agent"] == "fallback"
    assert report["confidence"] == "low"
    assert report["partial_reason"]
