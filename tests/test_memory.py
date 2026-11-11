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
    assert "RELATED_TO*0..3" in captured["query"]
    assert captured["params"]["current_cid"] == "conversation-current"


def test_memory_node_keeps_topics_for_immediate_ingest(monkeypatch):
    captured = {}
    monkeypatch.setattr(memory, "recall_decision", lambda *_: (True, "model_search"))
    monkeypatch.setattr(memory, "_extract_topics", lambda _text: ["office work"])

    def fake_recall(user_id, topics, *, current_conversation_id, query_text):
        captured.update(
            user_id=user_id,
            topics=topics,
            current_conversation_id=current_conversation_id,
            query_text=query_text,
        )
        return [{"text": "prior message", "topic": "office work", 'conversation_id':'previous'}]

    monkeypatch.setattr(memory, "recall_related", fake_recall)
    monkeypatch.setattr(memory, 'active_conversation_ids', lambda *_: {'previous'})
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
    assert captured['query_text'] == "Let's discuss office work"


def test_persist_enqueues_topics_with_the_source_turn(monkeypatch):
    topic_calls = []
    def save(*args, **kwargs):
        topic_calls.append(kwargs['memory_topics'])
        return 'user-message', 'assistant-message'
    monkeypatch.setattr(persist, 'save_turn', save)

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

    assert topic_calls == [['running']]


def test_empty_topic_extraction_still_searches_the_users_text(monkeypatch):
    monkeypatch.setattr(memory, 'recall_decision', lambda *_: (True, 'model_search'))
    monkeypatch.setattr(memory, '_extract_topics', lambda _text: [])
    captured = {}
    def search(uid, topics, **kwargs):
        captured.update(uid=uid, topics=topics, **kwargs)
        return [{'text':'Blue Bookshop is in Yeonnam-dong.', 'conversation_id':'previous'}]
    monkeypatch.setattr(memory, 'recall_related', search)
    monkeypatch.setattr(memory, 'active_conversation_ids', lambda *_: {'previous'})
    result = memory.memory_recall_node({'user_id':'alice','user_text':'Where is Blue Bookshop?'})
    assert result['retrieved_memory']
    assert captured['topics'] == []
    assert captured['query_text'] == 'Where is Blue Bookshop?'


def test_delete_memory_queries_are_user_scoped():
    calls = []

    class FakeTx:
        def run(self, query, **params):
            calls.append((query, params))
            from types import SimpleNamespace
            return SimpleNamespace(consume=lambda: None)

    ingest._delete_conversation_tx(FakeTx(), "conversation-1", "user-1")

    assert len(calls) == 6
    assert "GraphConversationFence" in calls[0][0]
    assert all("User {id: $uid}" in query for query, _params in calls[1:3])
    assert calls[1][1] == {"uid": "user-1", "cid": "conversation-1"}
