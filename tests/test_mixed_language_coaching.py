import pytest

from app.agents.nodes import ko_coach
from app.agents.nodes.router import route_by_lang
from app.agents.nodes.stt import stt_node


MIXED_INPUT = 'I went fukuoka about two times after broking with her. 지금은 그냥 좋은 추억으로 남기고 후쿠오카 자체를 좋아해요'


@pytest.mark.parametrize('text,expected', [
    (MIXED_INPUT, 'ko'),
    ('지금은 좋은 추억이에요. I went fukuoka about two times.', 'ko'),
    ('지금은 좋은 추억이에요.', 'ko'),
    ('I went to Fukuoka twice.', 'en'),
])
def test_mixed_text_keeps_full_input_on_korean_coaching_path(text, expected):
    state = {'user_text': text}
    state.update(stt_node(state))
    assert route_by_lang(state) == expected
    assert state['user_text'] == text


def test_coach_requests_whole_mixed_message_even_with_older_managed_prompt(monkeypatch):
    expression = "I went back to Fukuoka about twice after we broke up. Now I just treasure those memories and like Fukuoka itself."

    class Coach:
        def invoke(self, messages):
            assert messages[-1].content == MIXED_INPUT
            instruction = messages[0].content
            assert 'rewrite the ENTIRE latest user message' in instruction
            assert 'Correct grammar and improve phrasing in the existing English' in instruction
            assert 'Never omit an English sentence merely because it is already English.' in instruction
            assert 'check that each clause of the latest message is represented' in instruction
            return ko_coach.KoCoachOutput(reply='What do you like about Fukuoka?', english_expression=expression)

    monkeypatch.setattr(ko_coach, 'get_structured_llm', lambda _: Coach())
    monkeypatch.setattr(ko_coach, 'invoke_with_prompt', lambda llm, messages, *_: llm.invoke(messages))
    result = ko_coach.ko_coach_node({'system_prompt': 'English conversation tutor',
        'common_prompt_texts': {'talky/ko-coach': 'Translate the latest Korean utterance.'},
        'user_text': MIXED_INPUT})
    assert result['english_expression'] == expression
    assert result['ai_reply'] == 'What do you like about Fukuoka?'
