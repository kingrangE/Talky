from app.agents.nodes import memory, persist
from app.graph_db import ingest, recall


def test_memory_recall_is_scoped_and_excludes_current_conversation(monkeypatch):
    captured = {}

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def run(self, query, **params):
            captured["query"] = query
            captured["params"] = params
            return [{"text": "earlier", "lang": "en", "topic": "running"}]

    class FakeDriver:
        def session(self):
            return FakeSession()

    monkeypatch.setattr(recall, "ensure_schema", lambda: True)
    monkeypatch.setattr(recall, "get_driver", lambda: FakeDriver())

    result = recall.recall_related(
        "user-1", ["running"], current_conversation_id="conversation-current"
    )

    assert result[0]["text"] == "earlier"
    assert "(:User {id: $uid})-[:HAD]->(c:Conversation)" in captured["query"]
    assert "c.id <> $current_cid" in captured["query"]
    assert captured["params"]["current_cid"] == "conversation-current"


def test_memory_node_keeps_topics_for_immediate_ingest(monkeypatch):
    captured = {}
    monkeypatch.setattr(memory, "_extract_topics", lambda _text: ["office work"])

    def fake_recall(user_id, topics, *, current_conversation_id):
        captured.update(
            user_id=user_id,
            topics=topics,
            current_conversation_id=current_conversation_id,
        )
        return [{"text": "prior message", "topic": "office work"}]

    monkeypatch.setattr(memory, "recall_related", fake_recall)
    result = memory.memory_recall_node(
        {
            "user_id": "user-1",
            "conversation_id": "conversation-1",
            "user_text": "Let's discuss office work",
        }
    )

    assert result["memory_topics"] == ["office work"]
    assert result["retrieved_memory"][0]["text"] == "prior message"
    assert captured["current_conversation_id"] == "conversation-1"


def test_persist_ingests_topics_before_conversation_end(monkeypatch):
    message_ids = iter(["user-message", "assistant-message"])
    topic_calls = []
    monkeypatch.setattr(persist, "save_message", lambda *_args, **_kwargs: next(message_ids))
    monkeypatch.setattr(persist, "ingest_turn", lambda **_kwargs: True)
    monkeypatch.setattr(
        persist,
        "ingest_topics",
        lambda **kwargs: topic_calls.append(kwargs) or True,
    )

    persist.persist_node(
        {
            "conversation_id": "conversation-1",
            "user_id": "user-1",
            "user_text": "I went running",
            "ai_reply": "How did it go?",
            "language": "en",
            "memory_topics": ["running"],
        }
    )

    assert topic_calls == [
        {"conversation_id": "conversation-1", "topics": ["running"]}
    ]


def test_delete_memory_queries_are_user_scoped():
    calls = []

    class FakeTx:
        def run(self, query, **params):
            calls.append((query, params))

    ingest._delete_conversation_tx(FakeTx(), "conversation-1", "user-1")

    assert len(calls) == 4
    assert all("User {id: $uid}" in query for query, _params in calls[:2])
    assert calls[0][1] == {"uid": "user-1", "cid": "conversation-1"}
