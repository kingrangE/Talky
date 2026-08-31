from types import SimpleNamespace

from app.agents import evolve_graph
from app.db import seed


def test_seed_does_not_replace_evolved_active_prompt():
    evolved = SimpleNamespace(
        content="an evolved prompt that differs from the seed",
        rationale="rating-driven improvement",
        active=True,
    )

    class ScalarResult:
        def first(self):
            return evolved

    class FakeSession:
        def scalars(self, _stmt):
            return ScalarResult()

    assert seed.seed_prompt_v1(FakeSession()) is evolved
    assert evolved.active is True


def test_evolution_counts_only_ratings_for_active_prompt(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        evolve_graph,
        "get_active_prompt",
        lambda: {"id": "prompt-active", "content": "active", "version": 2},
    )

    def fake_count(prompt_version_id):
        captured["prompt_version_id"] = prompt_version_id
        return 5, {1: 0, 2: 0, 3: 0, 4: 1, 5: 4}

    monkeypatch.setattr(evolve_graph, "count_unconsumed_ratings", fake_count)
    result = evolve_graph.should_evolve_node({"threshold": 5})

    assert result["proceed"] is True
    assert result["current_prompt"]["id"] == "prompt-active"
    assert captured["prompt_version_id"] == "prompt-active"


def test_degraded_prompt_rolls_back_before_new_evolution(monkeypatch):
    class ScalarResult:
        def scalar(self):
            return True

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args, **_kwargs):
            return ScalarResult()

        def commit(self):
            return None

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    monkeypatch.setattr(evolve_graph, "get_engine", lambda: FakeEngine())
    monkeypatch.setattr(
        evolve_graph,
        "get_settings",
        lambda: SimpleNamespace(EVOLVE_BATCH=5, ROLLBACK_THRESHOLD=0.5),
    )
    monkeypatch.setattr(
        evolve_graph,
        "rollback_active_prompt_if_degraded",
        lambda **_kwargs: "parent-prompt",
    )
    monkeypatch.setattr(
        evolve_graph,
        "build_evolve_graph",
        lambda: (_ for _ in ()).throw(AssertionError("evolution must not run")),
    )

    result = evolve_graph.run_evolve_if_needed()

    assert result == {"action": "rolled_back", "prompt_id": "parent-prompt"}
