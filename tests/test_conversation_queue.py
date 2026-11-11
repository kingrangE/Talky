import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app import conversation as queue
from app.db import repo
from app.db.models import Conversation, ConversationTurn, GraphSyncEvent, Message, User


@pytest.fixture
def chat(pg_db):
    with pg_db[1]() as session:
        user = User(display_name='chat-test')
        session.add(user)
        session.flush()
        conv = Conversation(user_id=user.id)
        session.add(conv)
        session.flush()
        return str(user.id), str(conv.id)


def submit(chat, rid=None, text='Hello'):
    uid, cid = chat
    return queue.submit_turn(request_id=rid or str(uuid.uuid4()), conversation_id=cid,
                             user_id=uid, user_text=text)


def test_concurrent_retry_is_idempotent_and_owner_scoped(chat, pg_db):
    rid = str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(lambda _: submit(chat, rid), range(4)))
    assert {j['id'] for j in jobs} == {rid}
    with pg_db[1]() as session:
        assert session.scalar(select(func.count()).select_from(ConversationTurn)) == 1
    with pytest.raises(ValueError):
        submit(chat, rid, 'Different')
    with pytest.raises(ValueError):
        queue.get_turn(rid, str(uuid.uuid4()))
    with pytest.raises(ValueError, match='이전 응답'):
        submit(chat)


def test_lease_recovery_fences_old_attempt_and_commits_only_once(chat, pg_db):
    uid, cid = chat
    job = submit(chat)
    first = queue.claim_turn()
    assert first['attempt'] == 1
    with pg_db[1]() as session:
        session.get(ConversationTurn, uuid.UUID(job['id'])).lease_until = datetime.now(timezone.utc)-timedelta(seconds=1)
    second = queue.claim_turn()
    assert second['attempt'] == 2
    with pytest.raises(ValueError, match='Stale'):
        repo.save_turn(cid, uid, 'Hello', 'Hi', request_id=job['id'], expected_attempt=1)
    ids = repo.save_turn(cid, uid, 'Hello', 'Hi', request_id=job['id'], expected_attempt=2)
    assert repo.save_turn(cid, uid, 'Hello', 'Hi', request_id=job['id'], expected_attempt=2) == ids
    queue.fail_turn(first, 'old_failure')
    assert queue.get_turn(job['id'], uid)['status'] == 'processing'
    queue.process_turn(second)
    assert queue.get_turn(job['id'], uid)['status'] == 'completed'
    with pg_db[1]() as session:
        assert session.scalar(select(func.count()).select_from(Message)) == 2
        assert session.scalar(select(func.count()).select_from(GraphSyncEvent)) == 1


def test_text_is_committed_before_tts_failure(chat, monkeypatch):
    from app.agents import conversation_graph as graph
    from app.agents.nodes import tts
    graph.build_conversation_graph.cache_clear()
    monkeypatch.setattr(graph, 'load_conversation_prompt_node', lambda _: {})
    monkeypatch.setattr(graph, 'stt_node', lambda _: {'language': 'en'})
    monkeypatch.setattr(graph, 'memory_recall_node', lambda _: {})
    monkeypatch.setattr(graph, 'en_reply_node', lambda _: {'ai_reply': 'Hello there.'})
    monkeypatch.setattr(graph, 'better_expr_node', lambda _: {})
    monkeypatch.setattr(tts, 'synthesize', lambda *_: (_ for _ in ()).throw(RuntimeError('tts offline')))
    try:
        job = submit(chat)
        result = queue.process_turn(queue.claim_turn())
        assert result['ai_reply'] == 'Hello there.'
        assert queue.get_turn(job['id'], chat[0])['status'] == 'completed'
        assert len(repo.load_messages(chat[1], chat[0])) == 2
        monkeypatch.setattr(graph, 'build_conversation_graph', lambda: pytest.fail('completed request reran model'))
        assert queue.process_turn({**job, 'attempt': 1})['ai_reply'] == 'Hello there.'
    finally:
        graph.build_conversation_graph.cache_clear() if hasattr(graph.build_conversation_graph, 'cache_clear') else None


def test_global_queue_limit_and_expiry(chat, pg_db, monkeypatch):
    monkeypatch.setenv('CONVERSATION_QUEUE_LIMIT', '1')
    from app.settings import get_settings
    get_settings.cache_clear()
    job = submit(chat)
    with pg_db[1]() as session:
        conv = Conversation(user_id=uuid.UUID(chat[0]))
        session.add(conv)
        session.flush()
        other = (chat[0], str(conv.id))
    with pytest.raises(ValueError, match='요청이 많'):
        submit(other)
    with pg_db[1]() as session:
        session.get(ConversationTurn, uuid.UUID(job['id'])).queue_deadline = datetime.now(timezone.utc)-timedelta(seconds=1)
    assert queue.claim_turn() is None
    assert queue.get_turn(job['id'], chat[0])['status'] == 'failed'
    assert submit(other)['id']
