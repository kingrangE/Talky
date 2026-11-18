import pytest
from pydantic import ValidationError

from app.agents.nodes import better_expr

SUGGESTION = "I've been getting up around 11 lately and studying my major."
TRANSLATION = '저는 요즘 11시쯤 일어나서 전공 공부를 하고 있어요.'


def test_translation_alone_is_not_a_valid_learning_note():
    with pytest.raises(ValidationError):
        better_expr.BetterExpressionOutput(suggestion=SUGGESTION, note=TRANSLATION)


def test_rewrite_renders_translation_then_contextual_grammar_as_separate_bullets(monkeypatch):
    explanation = '현재완료진행형으로, 최근부터 지금까지 이어지거나 반복되는 행동을 말해요. 여기서는 요즘 11시쯤 일어나는 생활 패턴이 이어지고 있다는 뜻이에요.'
    output = better_expr.BetterExpressionOutput(suggestion=SUGGESTION, note={
        'translation':TRANSLATION,
        'explanations':[{'pattern':'have/has been ~ing', 'explanation':explanation}],
    })
    monkeypatch.setattr(better_expr, 'get_structured_llm', lambda _: object())
    monkeypatch.setattr(better_expr, 'invoke_with_prompt', lambda *_: output)
    result = better_expr.better_expr_node({'user_text':'These days I get up at about 11 and study for my major.'})
    assert result['better_expression'] == f'**{SUGGESTION}**\n\n- {TRANSLATION}\n- have/has been ~ing: {explanation}'


def test_note_cannot_omit_all_learning_points():
    with pytest.raises(ValidationError):
        better_expr.BetterExpressionOutput(suggestion=SUGGESTION,
            note={'translation':TRANSLATION, 'explanations':[]})


def test_unchanged_natural_sentence_does_not_force_a_lesson(monkeypatch):
    output = better_expr.BetterExpressionOutput(needs_improvement=False, suggestion='Good morning!',
        note={'translation':'좋은 아침이에요!', 'explanations':[]})
    monkeypatch.setattr(better_expr, 'get_structured_llm', lambda _: object())
    monkeypatch.setattr(better_expr, 'invoke_with_prompt', lambda *_: output)
    assert better_expr.better_expr_node({'user_text':'Good morning!'}) == {
        'better_expression':None, 'correction_status':'completed'}
