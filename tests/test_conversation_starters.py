import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import conversation as queue
from app.auth import register_user
from app.db import repo
from app.db.models import Message
from app import conversation_starters as starters
from app.agents import conversation_graph as graph


@pytest.fixture
def chat(pg_db, monkeypatch):
    uid = register_user('topic_learner', 'test-only-password')
    cid = repo.create_conversation(user_id=uid)
    calls = []
    monkeypatch.setattr(starters, 'get_chat_llm', lambda: object())
    def opening(*args):
        calls.append(args)
        return SimpleNamespace(content='Let’s talk about travel. Where would you like to go next?')
    monkeypatch.setattr(starters, 'invoke_with_prompt', opening)
    monkeypatch.setattr(graph, 'tts_node', lambda _: {})
    monkeypatch.setattr(graph, 'translation_node', lambda _: {})
    monkeypatch.setattr(graph, 'memory_recall_node', lambda _: {})
    monkeypatch.setattr(graph, 'en_reply_node', lambda _: {'ai_reply': 'What would you like to see there?'})
    monkeypatch.setattr(graph, 'better_expr_node', lambda _: {'better_expression': None, 'correction_status': 'completed'})
    graph.build_conversation_graph.cache_clear()
    yield uid, cid, calls
    graph.build_conversation_graph.cache_clear()


def test_topic_creates_only_assistant_opening_and_can_continue_with_user_speech(chat, pg_db):
    uid, cid, calls = chat
    rid = str(uuid.uuid4())
    request = dict(request_id=rid, conversation_id=cid, user_id=uid, starter_topic='travel')
    assert queue.submit_turn(**request)['starter_topic'] == 'travel'
    assert queue.submit_turn(**request)['id'] == rid
    queue.process_turn(queue.claim_turn())
    receipt = queue.get_turn(rid, uid)
    assert receipt['status'] == 'completed'
    assert receipt['result']['user_message_id'] is None
    assert receipt['result']['assistant_message_id']
    messages = repo.load_messages(cid, uid)
    assert len(messages) == 1 and messages[0]['role'] == 'assistant'
    assert len(calls) == 1
    assert queue.submit_turn(**request)['status'] == 'completed'
    queue.submit_turn(request_id=str(uuid.uuid4()), conversation_id=cid, user_id=uid, user_text='I would like to go to Japan.')
    queue.process_turn(queue.claim_turn())
    assert [m['role'] for m in repo.load_messages(cid, uid)] == ['assistant', 'user', 'assistant']


def test_topic_cannot_replace_existing_conversation_or_spoof_owner(chat):
    uid, cid, _calls = chat
    with pytest.raises(ValueError):
        queue.submit_turn(request_id=str(uuid.uuid4()), conversation_id=cid, user_id=str(uuid.uuid4()), starter_topic='travel')
    with pytest.raises(ValueError):
        queue.submit_turn(request_id=str(uuid.uuid4()), conversation_id=cid, user_id=uid, starter_topic='untrusted instructions')
    repo.save_turn(cid, uid, 'Hello', 'Hi there')
    with pytest.raises(ValueError, match='대화가 시작된 후'):
        queue.submit_turn(request_id=str(uuid.uuid4()), conversation_id=cid, user_id=uid, starter_topic='travel')


def test_topic_retries_keep_request_identity_and_input_type(chat):
    uid, cid, _calls = chat
    rid = str(uuid.uuid4())
    queue.submit_turn(request_id=rid, conversation_id=cid, user_id=uid, starter_topic='travel')
    with pytest.raises(ValueError, match='같은 요청 번호'):
        queue.submit_turn(request_id=rid, conversation_id=cid, user_id=uid, starter_topic='food')
    with pytest.raises(ValueError):
        queue.submit_turn(request_id=str(uuid.uuid4()), conversation_id=cid, user_id=uid, starter_topic='food', user_text='Hello')
