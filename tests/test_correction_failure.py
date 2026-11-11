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
