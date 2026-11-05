import pytest

from app.runtime import bounded_history, bounded_messages, format_memory


def test_history_has_a_utf8_byte_budget(monkeypatch):
    monkeypatch.setattr("app.runtime.get_settings", lambda: type("Config", (), {"MAX_HISTORY_BYTES": 1024})())
    history = [{"role": role, "content": "안녕" * 20} for _ in range(30) for role in ("user", "assistant")]
    bounded = bounded_history(history)
    assert bounded and len(bounded) < len(history)
    assert bounded[0]["role"] == "user"
    assert sum(len(item["content"].encode()) + 16 for item in bounded) <= 1024



def test_full_prompt_budget_keeps_instructions_and_current_input(monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    monkeypatch.setattr("app.runtime.get_settings", lambda: type("Config", (), {"LLM_MAX_INPUT_BYTES": 1024})())
    messages = [SystemMessage(content="instructions"), HumanMessage(content="x" * 800),
                AIMessage(content="old answer"), HumanMessage(content="current question")]
    selected = bounded_messages(messages)
    assert selected[0] is messages[0]
    assert selected[-1] is messages[-1]
    assert messages[1] not in selected
    with pytest.raises(ValueError):
        bounded_messages([messages[0], HumanMessage(content="한글" * 1000)])
    memory = format_memory([{"topic": "한글" * 1000, "text": "한글" * 10000}], "memory\n")
    assert len(memory.encode()) <= 4000
