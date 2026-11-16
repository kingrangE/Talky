from contextlib import nullcontext
import json

import httpx
from openai import OpenAI, RateLimitError
from pydantic import BaseModel, ValidationError
import pytest

from app.llm import capacity, factory
from app.settings import Settings
from app import warmup


class Coaching(BaseModel):
    expression: str
    explanation: str


@pytest.fixture
def configure(monkeypatch):
    cached_factory = factory.get_chat_llm
    cached_factory.cache_clear()
    monkeypatch.setattr(capacity, "resource_slot", lambda _: nullcontext())

    def setup(provider, **kwargs):
        cfg = Settings(_env_file=None, LLM_PROVIDER=provider, **kwargs)
        monkeypatch.setattr(factory, "get_settings", lambda: cfg)
        return cfg

    yield setup
    cached_factory.cache_clear()


@pytest.mark.parametrize("provider,key,host", [
    ("nvidia", "NVIDIA_API_KEY", "integrate.api.nvidia.com"),
    ("openai", "OPENAI_API_KEY", "api.openai.com"),
])
@pytest.mark.parametrize("streaming", [False, True])
def test_provider_payload_and_structured_korean_output(configure, monkeypatch, provider, key, host, streaming):
    cfg = configure(provider, **{key: "test-key"})
    llm = factory.get_chat_llm(streaming=streaming)
    assert str(llm.openai_api_base) == f"https://{host}/v1"
    captured = []

    def handle(request):
        assert request.url.host == host
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        captured.append(body)
        message = {"role": "assistant", "content": "You did a great job!"}
        if "tools" in body:
            message.update(content=None, tool_calls=[{
                "id": "call_1", "type": "function", "function": {
                    "name": "Coaching", "arguments": json.dumps({
                        "expression": "You did a great job!", "explanation": "상황에 맞춰 수고를 칭찬하는 표현이에요."
                    })
                }
            }])
        payload = {
            "id": "chat-test", "object": "chat.completion", "created": 1,
            "model": cfg.llm_model, "choices": [{"index": 0, "message": message,
                "finish_reason": "tool_calls" if "tools" in body else "stop"}]
        }
        if body["stream"]:
            if "tool_calls" in message:
                message["tool_calls"][0]["index"] = 0
            payload["object"] = "chat.completion.chunk"
            payload["choices"][0]["delta"] = payload["choices"][0].pop("message")
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                content="data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n")
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = OpenAI(api_key="test-key", base_url=llm.openai_api_base, http_client=http, max_retries=0)
        llm.client = client.chat.completions
        assert llm.invoke("수고했어를 영어로?").content == "You did a great job!"
        monkeypatch.setattr(factory, "get_chat_llm", lambda **_: llm)
        result = factory.get_structured_llm(Coaching).invoke("한국어로 설명해 주세요.")

    assert isinstance(result, Coaching)
    assert "수고" in result.explanation
    assert all(body["model"] == cfg.llm_model for body in captured)
    assert "tools" in captured[1]
    assert "response_format" not in captured[1]
    if provider == "nvidia":
        assert all(body["max_tokens"] == cfg.LLM_MAX_OUTPUT_TOKENS for body in captured)
        assert all("max_completion_tokens" not in body for body in captured)
        assert captured[0]["chat_template_kwargs"] == {"enable_thinking": False}
    else:
        assert captured[0]["max_completion_tokens"] == cfg.LLM_MAX_OUTPUT_TOKENS
        assert "chat_template_kwargs" not in captured[0]


@pytest.mark.parametrize("provider,key", [("nvidia", "NVIDIA_API_KEY"), ("openai", "OPENAI_API_KEY")])
def test_missing_key_fails_without_falling_back_to_another_provider(configure, provider, key):
    configure(provider, NVIDIA_API_KEY="", OPENAI_API_KEY="")
    with pytest.raises(ValueError, match=key):
        factory.get_chat_llm()


def test_nvidia_rate_limit_does_not_retry_or_call_paid_provider(configure):
    configure("nvidia", NVIDIA_API_KEY="test-key", OPENAI_API_KEY="unused-paid-key")
    llm = factory.get_chat_llm(streaming=False)
    calls = []

    def handle(request):
        calls.append(request.url.host)
        return httpx.Response(429, json={"error": {"message": "rate limited", "type": "rate_limit"}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = OpenAI(api_key="test-key", base_url=llm.openai_api_base, http_client=http, max_retries=llm.max_retries)
        llm.client = client.chat.completions
        with pytest.raises(RateLimitError):
            llm.invoke("Hello")
    assert calls == ["integrate.api.nvidia.com"]


@pytest.mark.parametrize("provider", ["nvidia", "openai"])
def test_remote_warmup_does_not_require_ollama(configure, monkeypatch, provider):
    cfg = configure(provider)
    monkeypatch.setattr(warmup, "get_settings", lambda: cfg)
    monkeypatch.setattr(warmup, "_model", lambda: None)
    monkeypatch.setattr(warmup, "_voice", lambda _: object())
    monkeypatch.setattr(warmup.urllib.request, "urlopen", lambda *_args, **_kwargs: pytest.fail("Ollama called"))
    warmup.warmup()


def test_local_provider_and_unknown_provider(configure):
    cfg = configure("ollama")
    assert factory.get_chat_llm().model == cfg.MODEL_NAME
    with pytest.raises(ValidationError):
        Settings(_env_file=None, LLM_PROVIDER="unknown")
