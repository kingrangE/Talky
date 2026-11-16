from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from app import api, conversation
from app.db.models import ConversationTurn, User
from app.mock_exam.privacy import audio_store, sign_recovery_token
from app.settings import get_settings


@pytest.fixture
def web(monkeypatch, tmp_path):
    monkeypatch.setenv('MOCK_EXAM_DATA_DIR', str(tmp_path))
    get_settings.cache_clear()
    audio_store.cache_clear()
    api.app.dependency_overrides.clear()
    with TestClient(api.app, raise_server_exceptions=False) as client:
        yield client
    api.app.dependency_overrides.clear()
    audio_store.cache_clear()


def test_auth_required_cookie_purpose_and_cross_origin_write(web):
    assert web.get('/api/conversations').status_code == 401
    web.cookies.set(api.COOKIE, sign_recovery_token(str(uuid.uuid4()), ttl_seconds=60))
    assert web.get('/api/conversations').status_code == 401
    assert web.post('/api/auth/logout', headers={'Origin': 'https://other.example'}).status_code == 403
    response = web.post('/api/auth/logout', headers={'Origin': 'http://testserver'})
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'


def test_upload_boundary_and_validation_do_not_echo_secrets(web, monkeypatch):
    monkeypatch.setattr(api, 'get_settings', lambda: SimpleNamespace(MAX_AUDIO_BYTES=128))
    assert web.put(f'/api/conversations/{uuid.uuid4()}/turns/{uuid.uuid4()}/voice', content=b'x' * 33000).status_code == 413
    secret = 'do-not-echo-this-password'
    response = web.post('/api/auth/login', json={'login_name': 'x' * 100, 'password': secret})
    assert response.status_code == 422
    assert secret not in response.text


def test_empty_voice_and_provider_failure_have_safe_explanations(web, monkeypatch):
    api.app.dependency_overrides[api.user_id] = lambda: str(uuid.uuid4())
    path = f'/api/conversations/{uuid.uuid4()}/turns/{uuid.uuid4()}/voice'
    response = web.put(path, content=b'')
    assert response.status_code == 400
    assert response.json()['error_code'] == 'audio_empty'
    assert '녹음된 음성이 없어요' in response.json()['detail']

    def timeout(*_args, **_kwargs):
        raise TimeoutError('SECRET provider payload /private/audio')
    monkeypatch.setattr(api.auth, 'authenticate', timeout)
    response = web.post('/api/auth/login', json={'login_name': 'learner', 'password': 'valid-password'})
    assert response.status_code == 503
    assert response.json()['error_code'] == 'request_timeout'
    assert '시간' in response.json()['detail']
    assert 'SECRET' not in response.text and '/private' not in response.text


def test_cookie_flags_and_failed_login(web, monkeypatch):
    monkeypatch.setattr(api.auth, 'authenticate', lambda *_: str(uuid.uuid4()))
    response = web.post('/api/auth/login', json={'login_name': 'learner', 'password': 'long-enough-password'})
    cookie = response.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie
    with TestClient(api.app, base_url='https://testserver') as secure:
        response = secure.post('/api/auth/login', json={'login_name': 'learner', 'password': 'long-enough-password'})
        assert 'secure' in response.headers['set-cookie'].lower()
    monkeypatch.setattr(api.auth, 'authenticate', lambda *_: None)
    assert web.post('/api/auth/login', json={'login_name': 'learner', 'password': 'wrong'}).status_code == 401


def test_api_hides_storage_paths_and_voice_scope(web, monkeypatch):
    uid, other = str(uuid.uuid4()), str(uuid.uuid4())
    api.app.dependency_overrides[api.user_id] = lambda: uid
    stored = {'id': str(uuid.uuid4()), 'conversation_id': str(uuid.uuid4()), 'status': 'processing',
              'input_text': '인식된 원문', 'result': {}, 'error_code': None,
              'input_audio_path': 'private/encrypted.fernet', 'output_audio_path': None}
    public = api.public_turn(stored)
    assert public['input_text'] == '인식된 원문'
    assert public['has_input_audio']
    assert 'private' not in str(public) and 'input_audio_path' not in public
    def owned(_rid, user):
        if user != other:
            raise ValueError('요청을 찾을 수 없습니다.')
        return stored
    monkeypatch.setattr(conversation, 'get_turn', owned)
    assert web.get(f"/api/turns/{stored['id']}/audio?kind=input").status_code == 400


def test_accounts_conversation_idempotence_and_owner_isolation(pg_db, web):
    engine, scope = pg_db
    credentials = {'login_name': 'web_learner', 'password': 'sufficient-password'}
    assert web.post('/api/auth/register', json=credentials).status_code == 201
    assert web.post('/api/auth/login', json=credentials).status_code == 200
    uid = web.get('/api/auth/me').json()['id']
    cid = web.post('/api/conversations').json()['id']
    rid = str(uuid.uuid4())
    path = f'/api/conversations/{cid}/turns/{rid}'
    first = web.put(path, json={'text': '번역 전에 보낸 원문'}).json()
    assert first['status'] == 'queued' and first['input_text'] == '번역 전에 보낸 원문'
    assert web.put(path, json={'text': '번역 전에 보낸 원문'}).json()['id'] == rid
    assert web.put(path, json={'text': '다른 내용'}).status_code == 400
    assert web.get(f'/api/conversations/{cid}').json()['turns'][0]['input_text'] == '번역 전에 보낸 원문'
    assert web.post(f'/api/conversations/{cid}/report').status_code == 400
    with scope() as session:
        assert len(session.scalars(select(ConversationTurn)).all()) == 1
    web.post('/api/auth/logout')
    assert web.get(f'/api/conversations/{cid}').status_code == 401
    other = {'login_name': 'another_learner', 'password': 'sufficient-password'}
    web.post('/api/auth/register', json=other)
    web.post('/api/auth/login', json=other)
    assert web.get(f'/api/conversations/{cid}').status_code == 400
    assert web.put(path, json={'text': '가로채기'}).status_code == 400
    assert web.get(f'/api/turns/{rid}/audio').status_code == 400
    assert web.get('/api/conversations').json() == []


@pytest.mark.parametrize('length', [7, 8, 24, 25])
def test_registration_enforces_password_length_on_server(pg_db, web, length):
    credentials = {'login_name': f'boundary_{length}', 'password': 'a' * length}
    response = web.post('/api/auth/register', json=credentials)
    assert response.status_code == (201 if 8 <= length <= 24 else 400)
    if response.status_code == 201:
        assert web.post('/api/auth/login', json=credentials).status_code == 200
    else:
        assert '8~24' in response.json()['detail']


def test_transcript_visible_before_translation_and_fenced_against_old_worker(pg_db):
    _engine, scope = pg_db
    from app.auth import register_user
    from app.db.repo import create_conversation
    uid = register_user('voice_learner', 'sufficient-password')
    cid = create_conversation(user_id=uid)
    rid = str(uuid.uuid4())
    conversation.submit_turn(request_id=rid, conversation_id=cid, user_id=uid, user_text='pending')
    job = conversation.claim_turn()
    conversation.record_transcript(rid, uid, job['attempt'], '번역 전에 인식된 원문')
    turn = conversation.get_turn(rid, uid)
    assert turn['input_text'] == '번역 전에 인식된 원문' and turn['result'] == {}
    with pytest.raises(ValueError, match='Stale'):
        conversation.record_transcript(rid, uid, job['attempt'] - 1, '이전 작업의 원문')
    with pytest.raises(ValueError, match='Stale'):
        conversation.record_transcript(rid, str(uuid.uuid4()), job['attempt'], '다른 사용자')
    assert conversation.get_turn(rid, uid)['input_text'] == '번역 전에 인식된 원문'


def test_exam_recovery_cannot_reuse_account_token(web):
    web.cookies.set('talky_exam', sign_recovery_token(f'account:{uuid.uuid4()}', ttl_seconds=60))
    assert web.get('/api/exam/session').status_code == 404
    assert web.post('/api/exam/recover', json={'token': sign_recovery_token(f'account:{uuid.uuid4()}', ttl_seconds=60)}).status_code == 400
    assert web.post('/api/exam/session', json={'consent': False}).status_code == 400
