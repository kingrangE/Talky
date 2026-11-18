import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading
import uuid

import pytest
from sqlalchemy import select

from app import conversation as queue, conversation_audio as speech
from app.audio import neural2
from app.db import repo
from app.db.models import Conversation, ConversationTurn, PendingAudioDeletion, User
from app.mock_exam.privacy import EncryptedAudioStore


@pytest.fixture
def turn(pg_db, monkeypatch, tmp_path):
    with pg_db[1]() as session:
        user = User(display_name='speech-test')
        session.add(user); session.flush()
        conv = Conversation(user_id=user.id)
        session.add(conv); session.flush()
        uid, cid = str(user.id), str(conv.id)
    job = queue.submit_turn(request_id=str(uuid.uuid4()), conversation_id=cid, user_id=uid, user_text='Hi')
    claimed = queue.claim_turn()
    assert claimed['id'] == job['id']
    repo.save_turn(cid, uid, 'Hi', 'Hello there. How are you?', request_id=job['id'], expected_attempt=claimed['attempt'])
    store = EncryptedAudioStore(tmp_path/'audio')
    monkeypatch.setattr(speech, 'audio_store', lambda: store)
    monkeypatch.setattr(queue, 'audio_store', lambda: store)
    monkeypatch.setattr(speech, 'reserve', lambda *_: None)
    state = {'request_id':job['id'], 'user_id':uid, 'turn_attempt':claimed['attempt'], 'ai_reply':'Hello there. How are you?'}
    return state, store, claimed


def test_sentence_boundaries_and_wav_roundtrip():
    assert neural2.segments('Dr. Kim paid 3.50 dollars. Is that right? Yes!') == [
        'Dr. Kim paid 3.50 dollars.', 'Is that right?', 'Yes!']
    assert neural2.segments('Try the U.S. option. Then relax.') == ['Try the U.S. option.', 'Then relax.']
    assert neural2.segments('a'*1001) == ['a'*1000, 'a']
    assert neural2.decode(neural2.encode(b'\x01\x00'*240)) == b'\x01\x00'*240
    with pytest.raises(ValueError):
        neural2.segments('x'*4001)


def test_first_sentence_streams_before_second_and_replay_does_not_synthesize(turn, monkeypatch):
    state, store, _ = turn
    entered, release = threading.Event(), threading.Event()
    calls = []
    first, second = b'\x01\x00'*240, b'\x02\x00'*240
    def synthesize(part):
        calls.append(part)
        if len(calls) == 2:
            entered.set(); assert release.wait(5)
            return second
        return first
    monkeypatch.setattr(neural2, 'synthesize', synthesize)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(speech.generate, state)
        try:
            assert entered.wait(3)
            current = queue.get_turn(state['request_id'], state['user_id'])
            from app.api import public_turn
            assert public_turn(current)['has_audio_stream']
            assert not public_turn(current)['has_output_audio']
            async def listen():
                stream = speech.stream(state['request_id'], state['user_id'], state['turn_attempt'])
                assert await anext(stream) == first
                release.set()
                output = b''.join([packet async for packet in stream])
                assert output == second
            asyncio.run(listen())
        finally:
            release.set()
        pending.result()
    current = queue.get_turn(state['request_id'], state['user_id'])
    assert current['status'] == 'processing'  # Coaching may still be running.
    assert public_turn(current)['has_output_audio']
    assert neural2.decode(store.load(current['output_audio_path'])) == first+second
    async def replay():
        return b''.join([packet async for packet in speech.stream(state['request_id'], state['user_id'], state['turn_attempt'])])
    assert asyncio.run(replay()) == first+second
    assert len(calls) == 2


def test_partial_failure_is_not_a_complete_file_and_stale_worker_cannot_publish(turn, monkeypatch, pg_db):
    state, store, _ = turn
    calls = []
    def synthesize(part):
        calls.append(part)
        if len(calls) == 2:
            raise TimeoutError('fake provider failure')
        return b'\0\0'*240
    monkeypatch.setattr(neural2, 'synthesize', synthesize)
    with pytest.raises(TimeoutError):
        speech.generate(state)
    current = queue.get_turn(state['request_id'], state['user_id'])
    assert current['output_audio_path'] is None
    assert current['result']['speech']['status'] == 'failed'
    with pg_db[1]() as session:
        assert len(session.scalars(select(PendingAudioDeletion)).all()) == 1
        row = session.get(ConversationTurn, uuid.UUID(state['request_id']))
        row.lease_until = datetime.now(timezone.utc)-timedelta(seconds=1)
    with pytest.raises(ValueError, match='Stale'):
        speech.publish(state, 'ready', neural2.encode(b'\0\0'*240))
    assert len(list(store.root.glob('*/*.fernet'))) == 1  # Rejected prefix removed immediately.
    next_job = queue.claim_turn()
    assert next_job['attempt'] == 2
    with pytest.raises(ValueError, match='Stale'):
        speech.publish(state, 'ready', neural2.encode(b'\0\0'*240))
    async def old_stream():
        await anext(speech.stream(state['request_id'], state['user_id'], 1))
    with pytest.raises(RuntimeError):
        asyncio.run(old_stream())


def test_retry_uses_committed_text_and_keeps_early_audio_when_turn_finishes(turn, monkeypatch):
    state, _, job = turn
    from app.settings import get_settings
    monkeypatch.setattr(get_settings(), 'CONVERSATION_TTS_PROVIDER', 'google_neural2')
    monkeypatch.setattr(neural2, 'synthesize', lambda _: b'\0\0'*240)
    queue.process_turn(job)
    current = queue.get_turn(state['request_id'], state['user_id'])
    assert current['status'] == 'completed'
    assert current['output_audio_path']
    assert current['result']['speech']['status'] == 'ready'


def test_insufficient_job_time_does_not_start_a_paid_call(turn, monkeypatch):
    from app.runtime import job_budget
    state, _, _ = turn
    def unexpected(*_):
        pytest.fail('must not reserve characters or start synthesis without time remaining')
    monkeypatch.setattr(speech, 'reserve', unexpected)
    monkeypatch.setattr(neural2, 'synthesize', unexpected)
    with job_budget(10), pytest.raises(TimeoutError, match='remaining job budget'):
        speech.generate(state)
    assert queue.get_turn(state['request_id'], state['user_id'])['result']['speech']['status'] == 'failed'


def test_stream_api_requires_owner_and_hides_incomplete_wav(turn, monkeypatch):
    from fastapi.testclient import TestClient
    from app import api
    state, store, _ = turn
    speech.publish(state, 'generating', neural2.encode(b'\0\0'*240))
    monkeypatch.setattr(api, 'audio_store', lambda: store)
    api.app.dependency_overrides[api.user_id] = lambda: state['user_id']
    try:
        with TestClient(api.app) as client:
            assert client.get(f"/api/turns/{state['request_id']}/audio").status_code == 404
            speech.publish(state, 'ready', neural2.encode(b'\0\0'*240))
            response = client.get(f"/api/turns/{state['request_id']}/audio-stream")
            assert response.status_code == 200 and response.content == b'\0\0'*240
            assert response.headers['cache-control'] == 'no-store'
            api.app.dependency_overrides[api.user_id] = lambda: str(uuid.uuid4())
            assert client.get(f"/api/turns/{state['request_id']}/audio-stream").status_code == 400
    finally:
        api.app.dependency_overrides.clear()


def test_speech_ready_while_translation_and_correction_are_blocked(turn, monkeypatch):
    from app.agents import conversation_graph as graph
    from app.settings import get_settings
    state, _, job = turn
    # Exercise a fresh graph turn rather than the committed-text recovery branch.
    from app.db.postgres import session_scope
    with session_scope() as session:
        session.get(ConversationTurn, uuid.UUID(job['id'])).result = None
    monkeypatch.setattr(get_settings(), 'CONVERSATION_TTS_PROVIDER', 'google_neural2')
    entered, release = threading.Event(), threading.Event()
    def slow_translation(_):
        entered.set(); assert release.wait(5)
        return {}
    monkeypatch.setattr(graph, 'load_conversation_prompt_node', lambda _: {})
    monkeypatch.setattr(graph, 'stt_node', lambda _: {'language':'en'})
    monkeypatch.setattr(graph, 'memory_recall_node', lambda _: {})
    monkeypatch.setattr(graph, 'en_reply_node', lambda _: {'ai_reply':'Hello there. How are you?'})
    monkeypatch.setattr(graph, 'translation_node', slow_translation)
    monkeypatch.setattr(graph, 'better_expr_node', lambda _: (release.wait(5) and {}) or {})
    monkeypatch.setattr(neural2, 'synthesize', lambda _: b'\0\0'*240)
    builder = graph.build_conversation_graph
    builder.cache_clear()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(queue.process_turn, job)
            try:
                assert entered.wait(3)
                import time
                deadline = time.monotonic()+3
                while time.monotonic() < deadline:
                    current = queue.get_turn(state['request_id'], state['user_id'])
                    if current['result'].get('speech', {}).get('status') == 'ready':
                        break
                    time.sleep(.01)
                assert current['result']['speech']['status'] == 'ready'
                assert current['status'] == 'processing'
                assert not pending.done()
            finally:
                release.set()
            pending.result()
    finally:
        builder.cache_clear()
