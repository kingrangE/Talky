import uuid

import pytest
from sqlalchemy import text

from app import reply_translation as translations
from app.auth import register_user
from app.db import repo
from app.db.models import Message
from app.errors import UserFacingError


@pytest.fixture
def reply(pg_db, monkeypatch):
    uid = register_user('translation_learner', 'test-only-password')
    cid = repo.create_conversation(user_id=uid)
    user, assistant = repo.save_turn(cid, uid, 'I visited a museum.', 'What did you like most?')
    calls = []

    class Model:
        def invoke(self, messages):
            calls.append(messages)
            return translations.KoreanTranslation(translation='무엇이 가장 좋았어요?')
    monkeypatch.setattr(translations, 'get_structured_llm', lambda *_args, **_kwargs: Model())
    return uuid.UUID(uid), uuid.UUID(user), uuid.UUID(assistant), calls


def test_translation_preserves_original_and_is_cached(pg_db, reply):
    uid, _user, assistant, calls = reply
    assert translations.translate_reply(assistant, uid) == '무엇이 가장 좋았어요?'
    assert translations.translate_reply(assistant, uid) == '무엇이 가장 좋았어요?'
    assert len(calls) == 1
    with pg_db[1]() as session:
        row = session.get(Message, assistant)
        assert row.content == 'What did you like most?'
        assert row.korean_translation == '무엇이 가장 좋았어요?'


def test_only_owner_can_translate_assistant_messages(reply):
    uid, user, assistant, calls = reply
    with pytest.raises(LookupError):
        translations.translate_reply(assistant, uuid.uuid4())
    with pytest.raises(LookupError):
        translations.translate_reply(user, uid)
    assert calls == []


def test_busy_translation_does_not_start_another_model_call(pg_db, reply):
    uid, _user, assistant, calls = reply
    with pg_db[0].connect() as connection:
        connection.execute(text('SELECT pg_advisory_lock(:key)'), {'key': translations.TRANSLATION_LOCK})
        connection.commit()
        try:
            with pytest.raises(UserFacingError, match='다른 요청'):
                translations.translate_reply(assistant, uid)
            assert calls == []
        finally:
            connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': translations.TRANSLATION_LOCK})
            connection.commit()


def test_failed_translation_can_be_retried_without_caching_error(pg_db, reply, monkeypatch):
    uid, _user, assistant, calls = reply
    original = translations.get_structured_llm
    class Broken:
        def invoke(self, _messages):
            raise TimeoutError('synthetic timeout')
    monkeypatch.setattr(translations, 'get_structured_llm', lambda *_args, **_kwargs: Broken())
    with pytest.raises(TimeoutError):
        translations.translate_reply(assistant, uid)
    with pg_db[1]() as session:
        assert session.get(Message, assistant).korean_translation is None
    monkeypatch.setattr(translations, 'get_structured_llm', original)
    assert translations.translate_reply(assistant, uid) == '무엇이 가장 좋았어요?'
    assert len(calls) == 1


def test_correction_is_stored_on_user_message(pg_db, reply):
    uid, user, assistant, _calls = reply
    with pg_db[1]() as session:
        cid = session.get(Message, user).conversation_id
    repo.save_correction(conversation_id=cid, user_id=uid, user_message_id=user,
                         assistant_message_id=assistant, better_expression='A useful correction')
    with pg_db[1]() as session:
        assert session.get(Message, user).better_expression == 'A useful correction'
        assert session.get(Message, assistant).better_expression is None
