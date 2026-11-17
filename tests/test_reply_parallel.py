from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.agents import conversation_graph as graph


@pytest.mark.parametrize('language,starter', [('en', False), ('ko', False), ('en', True)])
def test_translation_and_audio_start_before_optional_work_finishes(monkeypatch, language, starter):
    audio, translated, release = Event(), Event(), Event()
    reply = {'ai_reply': 'Hello!', 'assistant_message_id': 'assistant'}
    for name in ('load_conversation_prompt_node', 'stt_node', 'memory_recall_node',
                 'persist_node', 'persist_correction_node'):
        monkeypatch.setattr(graph, name, lambda _: {})
    for name in ('en_reply_node', 'ko_coach_node', 'starter_node'):
        monkeypatch.setattr(graph, name, lambda _: reply)

    def correction(_):
        assert release.wait(3)
        return {'better_expression': 'Hi!'}

    def tts(state):
        assert state['assistant_message_id'] == 'assistant'
        audio.set()
        assert release.wait(3)
        return {'audio_reply': b'wav'}

    def translation(state):
        assert state['assistant_message_id'] == 'assistant'
        translated.set()
        return {}

    monkeypatch.setattr(graph, 'better_expr_node', correction)
    monkeypatch.setattr(graph, 'tts_node', tts)
    monkeypatch.setattr(graph, 'translation_node', translation, raising=False)
    graph.build_conversation_graph.cache_clear()
    state = {'language': language, 'user_id': 'owner'}
    if starter:
        state['starter_topic'] = 'daily'
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(graph.build_conversation_graph().invoke, state)
            try:
                assert audio.wait(1), 'audio is waiting for optional correction'
                assert translated.wait(1), 'translation did not start alongside audio'
            finally:
                release.set()
            assert pending.result()['audio_reply'] == b'wav'
    finally:
        graph.build_conversation_graph.cache_clear()
