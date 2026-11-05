"""Exercise user isolation against a real relational store without an LLM/server."""

from contextlib import contextmanager
import uuid

import pytest
from sqlalchemy import JSON, MetaData, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.agents import evolve_graph
from app.db import repo, seed
from app.db.models import Base, User
from app.llm import prompts


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://")
    metadata = MetaData()
    for name in ("users", "prompt_versions", "conversations", "ratings", "reports"):
        table = Base.metadata.tables[name].to_metadata(metadata)
        for column in table.columns:
            if isinstance(column.type, JSONB):
                column.type = JSON()
        for index in table.indexes:
            if index.dialect_options["postgresql"].get("where") is not None:
                index.dialect_options["sqlite"]["where"] = text(str(index.dialect_options["postgresql"]["where"]))
    metadata.create_all(engine)

    @contextmanager
    def scope():
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            with session.begin():
                yield session

    monkeypatch.setattr(repo, "session_scope", scope)
    users = [uuid.uuid4(), uuid.uuid4()]
    with scope() as session:
        session.add_all([User(id=uid) for uid in users])
    yield scope, users
    engine.dispose()


def rate_conversation(user_id, stars):
    cid = repo.create_conversation(user_id=user_id)
    prompt = repo.get_conversation_prompt(cid, user_id)
    repo.save_report(conversation_id=cid, user_id=user_id, summary="summary", strengths=[], weaknesses=[], vocab_learned=[])
    repo.save_rating(conversation_id=cid, user_id=user_id, prompt_version_id=prompt["id"], stars=stars)
    return cid, prompt


def test_ratings_evolution_and_agent_prompt_are_user_owned(db):
    _, (alice, bob) = db
    cid, first = rate_conversation(alice, 5)
    _, bob_prompt = rate_conversation(bob, 1)
    assert repo.count_unconsumed_ratings(alice, first["id"])[0] == 1
    assert repo.count_unconsumed_ratings(bob, first["id"])[0] == 0
    assert repo.get_conversation_prompt(cid, bob) is None
    assert repo.get_unconsumed_rated_reports(user_id=bob, top_n=3, prompt_version_id=first["id"]) == []

    alice_state = evolve_graph.should_evolve_node({"user_id": str(alice), "threshold": 1})
    alice_state.update(evolve_graph.fetch_node({**alice_state, "user_id": str(alice), "threshold": 1}))
    bob_state = evolve_graph.should_evolve_node({"user_id": str(bob), "threshold": 2})
    assert alice_state["proceed"] is True
    assert bob_state["proceed"] is False
    result = evolve_graph.insert_node({
        **alice_state, "user_id": str(alice), "new_prompt_content": "Alice prefers short answers",
    })
    assert repo.get_active_prompt(alice)["id"] == result["new_prompt_id"]
    assert repo.get_active_prompt(bob)["id"] == bob_prompt["id"]
    assert repo.count_unconsumed_ratings(alice, first["id"])[0] == 0
    assert repo.count_unconsumed_ratings(bob, bob_prompt["id"])[0] == 1

    # 기존 대화는 RDB의 고정 버전, 새 대화는 개선 버전을 사용한다.
    loaded = prompts.load_conversation_prompt_node({
        "user_id": str(alice), "conversation_id": cid, "system_prompt": "spoofed",
    })
    assert loaded == {"prompt_version_id": first["id"], "system_prompt": first["content"], "history": []}
    new_cid = repo.create_conversation(user_id=alice)
    assert prompts.load_conversation_prompt_node({
        "user_id": str(alice), "conversation_id": new_cid,
    })["system_prompt"] == "Alice prefers short answers"
    with pytest.raises(prompts.ActivePromptMissing):
        prompts.load_conversation_prompt_node({"user_id": str(bob), "conversation_id": cid})


def test_reject_foreign_prompt_parent_and_rating(db):
    _, (alice, bob) = db
    cid, prompt = rate_conversation(alice, 5)
    with pytest.raises(ValueError, match="belong"):
        repo.save_rating(conversation_id=cid, user_id=bob, prompt_version_id=prompt["id"], stars=1)
    with pytest.raises(ValueError, match="Parent"):
        repo.insert_prompt_version(user_id=bob, content="wrong", rationale=None, parent_id=prompt["id"])
    with pytest.raises(ValueError, match="conversation's"):
        repo.save_rating(conversation_id=cid, user_id=alice, prompt_version_id=str(uuid.uuid4()), stars=1)


def test_rollback_only_changes_own_prompt_and_ratings(db):
    _, (alice, bob) = db
    _, parent = rate_conversation(alice, 5)
    new_id = repo.insert_prompt_version(user_id=alice, content="English tutor", rationale="evolved", parent_id=parent["id"])
    rate_conversation(alice, 1)
    _, bob_prompt = rate_conversation(bob, 1)
    rolled_back = repo.rollback_active_prompt_if_degraded(user_id=alice, min_samples=1, drop_threshold=0.5)
    assert rolled_back == parent["id"]
    assert repo.get_active_prompt(alice)["id"] == parent["id"]
    assert repo.get_active_prompt(bob)["id"] == bob_prompt["id"]
    assert repo.count_unconsumed_ratings(alice, new_id)[0] == 0
    assert repo.count_unconsumed_ratings(bob, bob_prompt["id"])[0] == 1


def test_seed_preserves_each_users_evolved_prompt(db):
    scope, (alice, bob) = db
    _, first = rate_conversation(alice, 5)
    new_id = repo.insert_prompt_version(user_id=alice, content="English tutor for Alice", rationale="evolved", parent_id=first["id"])
    with scope() as session:
        seed.seed_prompt_v1(session, alice)
        seed.seed_prompt_v1(session, bob)
    assert repo.get_active_prompt(alice)["id"] == new_id
    assert repo.get_active_prompt(bob)["version"] == 1


@pytest.mark.parametrize("user_text", ["Hello", "안녕하세요"])
def test_agent_loads_own_rdb_prompt_before_calling_llm(db, monkeypatch, user_text):
    from types import SimpleNamespace
    from app.agents import conversation_graph
    from app.agents.nodes import en_reply, ko_coach

    _, (alice, _) = db
    cid = repo.create_conversation(user_id=alice)
    prompt = repo.get_conversation_prompt(cid, alice)
    calls = []

    class FakeLLM:
        def invoke(self, messages):
            calls.append(messages)
            return SimpleNamespace(content="Hello!", reply="Hello!", english_expression="Hello")

    monkeypatch.setattr(en_reply, "get_chat_llm", lambda: FakeLLM())
    monkeypatch.setattr(ko_coach, "get_structured_llm", lambda _schema: FakeLLM())
    for name in ("memory_recall_node", "better_expr_node", "tts_node", "persist_node"):
        monkeypatch.setattr(conversation_graph, name, lambda _state: {})
    conversation_graph.build_conversation_graph.cache_clear()
    try:
        result = conversation_graph.build_conversation_graph().invoke({
            "conversation_id": cid, "user_id": str(alice), "user_text": user_text,
            "system_prompt": "untrusted prompt", "prompt_version_id": "untrusted version",
        })
    finally:
        conversation_graph.build_conversation_graph.cache_clear()
    assert len(calls) == 1
    assert calls[0][0].content.startswith(prompt["content"])
    assert result["prompt_version_id"] == prompt["id"]
