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


@pytest.mark.parametrize("correction_error", [False, True])
def test_text_is_committed_before_tts_failure(chat, monkeypatch, correction_error):
    from app.agents import conversation_graph as graph
    from app.agents.nodes import tts
    builder = graph.build_conversation_graph
    builder.cache_clear()
    monkeypatch.setattr(graph, 'load_conversation_prompt_node', lambda _: {})
    monkeypatch.setattr(graph, 'stt_node', lambda _: {'language': 'en'})
    monkeypatch.setattr(graph, 'memory_recall_node', lambda _: {})
    monkeypatch.setattr(graph, 'en_reply_node', lambda _: {'ai_reply': 'Hello there.'})
    if correction_error:
        from app.agents.nodes import better_expr
        monkeypatch.setattr(better_expr, 'get_structured_llm', lambda _: (_ for _ in ()).throw(ValueError('invalid JSON')))
    else:
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
        builder.cache_clear()


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


def test_audio_expiry_preserves_live_audio_and_retries_failed_deletion(chat, pg_db, tmp_path, monkeypatch):
    from app.mock_exam import service
    from app.mock_exam.privacy import EncryptedAudioStore
    from app.db.models import PendingAudioDeletion
    from app.mock_exam.repository import referenced_audio_paths
    store = EncryptedAudioStore(tmp_path/'audio')
    monkeypatch.setattr(service, 'audio_store', lambda: store)
    expired = store.save(chat[1], 1, b'expired')
    live = store.save(chat[1], 1, b'live')
    now = datetime.now(timezone.utc)
    with pg_db[1]() as session:
        for path, expiry in [(expired, now-timedelta(hours=1)), (live, now+timedelta(hours=1))]:
            session.add(ConversationTurn(id=uuid.uuid4(), conversation_id=uuid.UUID(chat[1]),
                user_id=uuid.UUID(chat[0]), input_digest='a'*64, status='completed', input_audio_path=path,
                queue_deadline=now, audio_expires_at=expiry))
    delete = store.delete
    monkeypatch.setattr(store, 'delete', lambda _: (_ for _ in ()).throw(OSError('injected')))
    service.run_expiry_cleanup()
    assert referenced_audio_paths() == {live}
    with pg_db[1]() as session:
        assert session.get(PendingAudioDeletion, expired) is not None
    assert store.load(live) == b'live'
    monkeypatch.setattr(store, 'delete', delete)
    service.run_expiry_cleanup()
    assert not (store.root/expired).exists()
    assert store.load(live) == b'live'


def test_streamlit_submits_polls_and_displays_committed_worker_result(chat, monkeypatch):
    import sys
    from streamlit.testing.v1 import AppTest
    monkeypatch.setitem(sys.modules, '__main__', sys.modules['__main__'])
    app = AppTest.from_file('main.py')
    app.session_state.auth_user_id = chat[0]
    app.session_state.conversation_id = chat[1]
    app.run(timeout=15)
    assert not app.exception
    app.chat_input[0].set_value('Hello from UI').run(timeout=15)
    assert not app.exception
    rid = app.session_state.pending_turn_id
    assert queue.get_turn(rid, chat[0])['status'] == 'queued'
    assert app.chat_input[0].disabled
    job = queue.claim_turn()
    repo.save_turn(chat[1], chat[0], 'Hello from UI', 'Reply from worker',
                   request_id=rid, expected_attempt=job['attempt'])
    app.run(timeout=15)
    assert [m['content'] for m in app.session_state.messages] == ['Hello from UI', 'Reply from worker']
    assert app.session_state.pending_turn_id == rid
    assert app.chat_input[0].disabled
    queue.process_turn(job)
    app.run(timeout=15)
    assert not app.exception
    assert app.session_state.pending_turn_id is None
    assert not app.chat_input[0].disabled
    contents = [m['content'] for m in app.session_state.messages]
    assert contents == ['Hello from UI', 'Reply from worker']


def test_user_quota_preserves_other_users_capacity(chat, pg_db, monkeypatch):
    monkeypatch.setenv('CONVERSATION_USER_QUEUE_LIMIT', '1')
    from app.settings import get_settings
    get_settings.cache_clear()
    first = submit(chat)
    assert submit(chat, first['id'])['id'] == first['id']
    with pg_db[1]() as session:
        other_user = User(display_name='other')
        session.add(other_user)
        session.flush()
        own = Conversation(user_id=uuid.UUID(chat[0]))
        other = Conversation(user_id=other_user.id)
        session.add_all([own, other])
        session.flush()
        own_chat, other_chat = (chat[0], str(own.id)), (str(other_user.id), str(other.id))
    with pytest.raises(ValueError, match='진행 중인 요청'):
        submit(own_chat)
    assert submit(other_chat)['id']
    job = queue.claim_turn()
    repo.save_turn(chat[1], chat[0], 'Hello', 'Hi', request_id=job['id'], expected_attempt=job['attempt'])
    queue.process_turn(job)
    assert submit(own_chat)['id']


def test_permanent_failure_stops_and_transient_failure_waits(chat, pg_db):
    from datetime import datetime, timezone
    first = submit(chat)
    claimed = queue.claim_turn()
    queue.fail_turn(claimed, ValueError('invalid input'))
    assert queue.get_turn(first['id'], chat[0])['status'] == 'failed'
    second = submit(chat)
    claimed = queue.claim_turn()
    queue.fail_turn(claimed, TimeoutError('temporary'))
    assert queue.get_turn(second['id'], chat[0])['status'] == 'queued'
    assert queue.claim_turn() is None
    with pg_db[1]() as session:
        session.get(ConversationTurn, uuid.UUID(second['id'])).lease_until = datetime.now(timezone.utc)
    assert queue.claim_turn()['attempt'] == 2
