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
    (True, 'I went to a museum yesterday.', '**Try:**'),
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
