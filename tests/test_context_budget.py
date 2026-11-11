import pytest
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from app.runtime import bounded_messages
from app.settings import get_settings
from app.context_budget import input_budget


@pytest.mark.parametrize('text', ['한글 대화 기억 '*2000, 'English memory '*2000, '한글 English '*2000])
def test_context_reserves_output_and_preserves_current_input(text):
    messages = [SystemMessage(content='Follow evidence.'), HumanMessage(content=text),
                AIMessage(content='Earlier reply'), HumanMessage(content='What do you remember?')]
    result = bounded_messages(messages)
    assert result[0] == messages[0] and result[-1] == messages[-1]
    assert result[1].type == 'human'
    assert sum(len(m.content.encode())+64 for m in result) <= input_budget()
    cfg = get_settings()
    assert input_budget()+cfg.LLM_MAX_OUTPUT_TOKENS+2048 <= cfg.LLM_CONTEXT_TOKENS


def test_invalid_context_output_combination_is_rejected(monkeypatch):
    monkeypatch.setenv('LLM_CONTEXT_TOKENS','4096')
    monkeypatch.setenv('LLM_MAX_OUTPUT_TOKENS','4096')
    get_settings.cache_clear()
    with pytest.raises(ValueError, match='reserve'):
        bounded_messages([HumanMessage(content='Hi')])
