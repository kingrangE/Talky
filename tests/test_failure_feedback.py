import pytest

from app.agents.nodes import stt
from app.errors import UserFacingError, error_code, failure_message, input_message
from app.retry import error_name, retry_delay


def test_silent_speech_is_rejected_before_the_reply_graph(monkeypatch):
    monkeypatch.setattr(stt, 'transcribe', lambda _: ('   ', 'en'))
    with pytest.raises(ValueError, match='목소리|말소리'):
        stt.stt_node({'audio_bytes': b'test recording'})


@pytest.mark.parametrize('status,expected,word', [
    (429, 'rate_limited', '이용 한도'), (401, 'service_authentication', '인증'),
    (403, 'service_authentication', '인증'), (408, 'request_timeout', '시간'),
    (404, 'service_configuration', '설정'), (500, 'service_unavailable', '응답'),
    (503, 'service_unavailable', '응답'),
])
def test_provider_errors_are_classified_without_exposing_payload(status, expected, word):
    class ProviderError(Exception):
        status_code = status
    error = ProviderError('SECRET API KEY /private/audio.wav private user text')
    assert error_name(error) == expected
    assert word in failure_message(error)
    assert 'SECRET' not in failure_message(error)
    assert '/private' not in failure_message(error)


@pytest.mark.parametrize('error,code', [
    (TimeoutError('SECRET'), 'request_timeout'),
    (ConnectionError('SECRET'), 'connection_failed'),
    (FileNotFoundError('SECRET'), 'resource_unavailable'),
    (ValueError('SECRET'), 'invalid_input'),
    (TypeError('SECRET'), 'unexpected_error'),
    ('worker_interrupted', 'worker_interrupted'),
    ('queue_expired', 'queue_expired'),
    ('stt_empty_with_signal', 'speech_not_detected'),
    ('RateLimitError', 'rate_limited'),
    (None, 'unexpected_error'),
    ('SECRET UNKNOWN CODE', 'unexpected_error'),
])
def test_known_legacy_and_unknown_errors_have_safe_fallback(error, code):
    assert error_code(error) == code
    assert failure_message(error)
    assert 'SECRET' not in failure_message(error)


def test_unreadable_recording_does_not_retry_its_io_cause():
    from app.audio.validation import validate_audio
    with pytest.raises(UserFacingError) as caught:
        validate_audio(b'not an audio file' * 20)
    assert error_name(caught.value) == 'audio_unreadable'
    assert retry_delay(caught.value, 1) is None


def test_only_reviewed_validation_messages_can_pass_through():
    assert '8~24' in input_message(ValueError('비밀번호는 8~24자여야 합니다.'))
    assert 'SECRET' not in input_message(ValueError('SECRET customer data'))


def test_silent_turn_persists_cause_and_never_calls_llm(pg_db, monkeypatch):
    from app.auth import register_user
    from app import conversation
    from app.db.repo import create_conversation
    from app.agents.nodes import en_reply, ko_coach, memory
    uid = register_user('silent_learner', 'test-only-password')
    cid = create_conversation(user_id=uid)
    import uuid
    rid = str(uuid.uuid4())
    conversation.submit_turn(request_id=rid, conversation_id=cid, user_id=uid, user_text='placeholder')
    job = conversation.claim_turn()
    # Use the real graph and worker failure persistence, replacing only recognition.
    job['input_audio_path'] = 'synthetic-recording'
    monkeypatch.setattr(conversation, 'audio_store', lambda: type('Store', (), {'load': lambda *_: b'fixture'})())
    monkeypatch.setattr(stt, 'transcribe', lambda _: ('', 'en'))
    def no_model(*_args, **_kwargs):
        pytest.fail('silent recordings must not call the model')
    monkeypatch.setattr(en_reply, 'get_chat_llm', no_model)
    monkeypatch.setattr(ko_coach, 'get_structured_llm', no_model)
    monkeypatch.setattr(memory, 'get_structured_llm', no_model)
    with pytest.raises(UserFacingError) as caught:
        conversation.process_turn(job)
    conversation.fail_turn(job, caught.value)
    receipt = conversation.get_turn(rid, uid)
    assert receipt['status'] == 'failed' and receipt['error_code'] == 'speech_not_detected'
    assert '목소리' in failure_message(receipt['error_code'])
