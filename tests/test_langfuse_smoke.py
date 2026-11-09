from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import langfuse_smoke as smoke


@pytest.mark.parametrize("reply", ["Sounds like a long day!", "", TimeoutError("model unavailable")])
def test_smoke_records_output_and_flushes_on_failure(monkeypatch, reply):
    generation = SimpleNamespace(trace_id="a" * 32, update=Mock())

    @contextmanager
    def observation(**kwargs):
        assert kwargs["prompt"].version == 1
        assert kwargs["input"][0]["content"] == "Reply in English."
        assert kwargs["input"][1]["content"] == "I had a busy day at work."
        yield generation

    client = SimpleNamespace(
        auth_check=lambda: True, start_as_current_observation=observation,
        flush=Mock(), get_trace_url=Mock(return_value="https://example.test/trace"),
    )
    llm = Mock()
    if isinstance(reply, Exception):
        llm.invoke.side_effect = reply
    else:
        llm.invoke.return_value = SimpleNamespace(content=reply)
    monkeypatch.setattr(smoke, "get_client", lambda: client)
    monkeypatch.setattr(smoke, "remote_prompt", lambda name, version: SimpleNamespace(name=name, version=version, prompt="Reply in English."))
    monkeypatch.setattr(smoke, "get_chat_llm", lambda **kwargs: llm)
    monkeypatch.setattr(smoke, "get_settings", lambda: SimpleNamespace(use_openai=False, MODEL_NAME="test-model"))
    if isinstance(reply, Exception):
        with pytest.raises(TimeoutError):
            smoke.main()
    elif not reply:
        with pytest.raises(ValueError):
            smoke.main()
    else:
        assert smoke.main() == generation.trace_id
        generation.update.assert_called_once_with(output=reply)
    llm.invoke.assert_called_once()
    client.flush.assert_called_once()


def test_disabled_smoke_does_not_call_a_model(monkeypatch):
    monkeypatch.setattr(smoke, "get_client", lambda: None)
    model = Mock()
    monkeypatch.setattr(smoke, "get_chat_llm", model)
    with pytest.raises(SystemExit, match="LANGFUSE_ENABLED=true"):
        smoke.main()
    model.assert_not_called()
