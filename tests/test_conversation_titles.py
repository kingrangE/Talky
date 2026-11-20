from types import SimpleNamespace

import pytest

from app.agents.nodes import title as naming
from app.auth import register_user
from app.db import repo


@pytest.mark.parametrize('outcome', ['automatic', 'manual_during_generation', 'model_unavailable'])
def test_title_generation_preserves_manual_edits_and_survives_model_failure(pg_db, monkeypatch, outcome):
    user = register_user('title_learner', 'test-only-password')
    cid = repo.create_conversation(user_id=user)
    text = 'I want to learn how to make fresh pasta.'
    user_message, _ = repo.save_turn(cid, user, text, 'What kind of pasta would you like to make?')

    def generate(messages):
        assert text in messages[-1].content
        if outcome == 'model_unavailable':
            raise TimeoutError('model timed out')
        if outcome == 'manual_during_generation':
            repo.update_conversation_title(cid, '내 파스타 수업', user)
        return SimpleNamespace(content='“파스타 만들기”')

    monkeypatch.setattr(naming, 'get_chat_llm', lambda **_: SimpleNamespace(invoke=generate))
    state = {'conversation_id': cid, 'user_id': user, 'user_text': text,
             'user_message_id': user_message, 'ai_reply': 'What kind of pasta would you like to make?'}
    naming.title_node(state)
    expected = {'automatic': '파스타 만들기', 'manual_during_generation': '내 파스타 수업', 'model_unavailable': text}
    assert repo.list_conversations(user)[0]['title'] == expected[outcome]
    monkeypatch.setattr(naming, 'get_chat_llm', lambda **_: pytest.fail('named conversations should skip generation'))
    naming.title_node(state)
