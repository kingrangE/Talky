import pytest
from app.agents.nodes import better_expr


@pytest.mark.parametrize('error', [TimeoutError('timeout'), ValueError('invalid JSON')])
def test_optional_correction_failure_is_explicit(monkeypatch, error):
    class Broken:
        def invoke(self, *_args, **_kwargs):
            raise error
    monkeypatch.setattr(better_expr, 'get_structured_llm', lambda _: Broken())
    monkeypatch.setattr(better_expr, 'invoke_with_prompt', lambda llm, *args: llm.invoke())
    result = better_expr.better_expr_node({'user_text': 'Hello'})
    assert result == {'better_expression': None, 'correction_status': 'failed'}


@pytest.mark.parametrize('needs_improvement,suggestion,expected', [
    (False, 'I visited the museum yesterday.', None),
    (True, 'I went to the museum yesterday.', None),
    (True, 'I went to a museum yesterday.', '**I went to a museum yesterday.**'),
])
def test_natural_expressions_do_not_generate_a_correction(monkeypatch, needs_improvement, suggestion, expected):
    result = better_expr.BetterExpressionOutput(needs_improvement=needs_improvement, suggestion=suggestion, note='설명')
    monkeypatch.setattr(better_expr, 'get_structured_llm', lambda _: object())
    monkeypatch.setattr(better_expr, 'invoke_with_prompt', lambda *_: result)
    correction = better_expr.better_expr_node({'user_text': 'I went to the museum yesterday.'})
    assert correction['correction_status'] == 'completed'
    if expected is None:
        assert correction['better_expression'] is None
    else:
        assert correction['better_expression'].startswith(expected)


def test_speech_formatting_does_not_create_a_false_correction(monkeypatch):
    result = better_expr.BetterExpressionOutput(needs_improvement=True,
        suggestion="Hi, I'm studying English. Can you help me?", note='대문자와 구두점을 정리했어요.')
    monkeypatch.setattr(better_expr, 'get_structured_llm', lambda _: object())
    monkeypatch.setattr(better_expr, 'invoke_with_prompt', lambda *_: result)
    assert better_expr.better_expr_node({'user_text':"Hi I'm studying english Can you help me?", 'audio_bytes':b'fixture'})['better_expression'] is None


def test_correction_preserves_bold_sentence_and_translation_first_bullets(monkeypatch):
    suggestion = "I usually can't get up early, so I end up getting up after 12."
    note = '- 저는 보통 일찍 일어나지 못해서 결국 12시가 지나서 일어나게 돼요.\n- end up ~ing: 결국 ~하게 되다.'
    output = better_expr.BetterExpressionOutput(suggestion=suggestion, note=note)
    monkeypatch.setattr(better_expr, 'get_structured_llm', lambda _: object())
    monkeypatch.setattr(better_expr, 'invoke_with_prompt', lambda *_: output)
    result = better_expr.better_expr_node({'user_text': 'I wake up late after 12 because I cannot wake early.'})
    assert result['better_expression'] == f'**{suggestion}**\n\n{note}'


def test_managed_prompt_also_receives_translation_first_format():
    messages = better_expr._build_messages({'user_text': 'I wake late.',
        'common_prompt_texts': {'talky/better-expression': 'Previously published coaching prompt.'}})
    assert 'The first bullet must translate the entire suggestion into Korean.' in messages[0].content
    assert 'actually used in the suggestion' in messages[0].content


@pytest.mark.parametrize('original,suggestion,expression', [
    ('I have difficulty getting out of bed in the morning.',
     'I have a hard time getting out of bed in the morning.', 'have a hard time ~ing: ~하는 데 어려움을 겪다.'),
    ('I am not accustomed to speaking English.',
     "I'm not used to speaking English.", 'be used to ~ing: ~하는 데 익숙하다.'),
])
def test_grammatical_input_can_receive_a_conversational_rewrite(monkeypatch, original, suggestion, expression):
    class Coach:
        def invoke(self, messages):
            instruction = messages[0].content
            assert 'Do not stop because the grammar is correct.' in instruction
            assert 'set needs_improvement=true' in instruction
            assert suggestion in instruction
            return better_expr.BetterExpressionOutput(needs_improvement=True,
                suggestion=suggestion, note=f'- 개선 문장의 한국어 해석.\n- {expression}')

    monkeypatch.setattr(better_expr, 'get_structured_llm', lambda _: Coach())
    monkeypatch.setattr(better_expr, 'invoke_with_prompt', lambda llm, messages, *_: llm.invoke(messages))
    result = better_expr.better_expr_node({'user_text': original,
        'common_prompt_texts': {'talky/better-expression': 'Previously published grammar coaching prompt.'}})
    assert result['correction_status'] == 'completed'
    assert result['better_expression'].startswith(f'**{suggestion}**\n\n- ')
    assert expression in result['better_expression']
