from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langfuse.api import NotFoundError
from langfuse.api.prompts.types.prompt import Prompt_Text
from langfuse.model import TextPromptClient

from app.llm import manage_prompts, prompt_registry as registry
from app.settings import get_settings


class FakeCloud:
    def __init__(self):
        self.prompts = {}
        self.calls = []
        self.observations = []

    def get_prompt(self, name, *, version=None, label=None, **kwargs):
        self.calls.append((name, version, label))
        if version is not None and (name, version) in self.prompts:
            return self.prompts[name, version]
        matches = [p for (n, _), p in self.prompts.items() if n == name and label in p.labels]
        if matches:
            return max(matches, key=lambda p: p.version)
        raise NotFoundError(body="not found")

    def create_prompt(self, *, name, prompt, labels, config=None, **kwargs):
        version = max((v for n, v in self.prompts if n == name), default=0) + 1
        for (n, _), previous in self.prompts.items():
            if n == name:
                previous.labels = [label for label in previous.labels if label not in [*labels, "latest"]]
        created = TextPromptClient(Prompt_Text(
            type="text", name=name, version=version, prompt=prompt,
            labels=[*labels, "latest"], config=config or {}, tags=[],
        ))
        self.prompts[name, version] = created
        return created

    def update_prompt(self, *, name, version, new_labels):
        for (n, v), prompt in self.prompts.items():
            if n == name:
                prompt.labels = [label for label in prompt.labels if label not in new_labels]
        self.prompts[name, version].labels.extend(new_labels)

    def start_observation(self, **kwargs):
        record = {"start": kwargs}
        self.observations.append(record)
        return SimpleNamespace(update=lambda **values: record.update(values), end=lambda: record.update(ended=True))


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PROMPT_VERSIONS", "{}")
    get_settings.cache_clear()
    registry.get_client.cache_clear()
    client = FakeCloud()
    monkeypatch.setattr(registry, "get_client", lambda: client)
    monkeypatch.setattr(manage_prompts, "get_client", lambda: client)
    for name in registry.CATALOG:
        client.create_prompt(name=name, prompt=registry.builtin_text(name), labels=["production"])
    yield client
    get_settings.cache_clear()


def test_numeric_version_never_follows_a_moving_label(cloud, monkeypatch):
    reference = registry.resolve_reference("talky/en-reply")
    cloud.create_prompt(name="talky/en-reply", prompt="Changed instructions", labels=["production"])
    assert registry.read_reference(reference) == registry.builtin_text("talky/en-reply")
    assert registry.resolve_reference("talky/en-reply")["version"] == 2
    monkeypatch.setenv("LANGFUSE_PROMPT_VERSIONS", '{"talky/en-reply":1}')
    get_settings.cache_clear()
    assert registry.resolve_reference("talky/en-reply")["version"] == 1
    assert cloud.calls[-1] == ("talky/en-reply", 1, None)


def test_missing_cloud_version_cannot_silently_use_a_builtin(cloud):
    with pytest.raises(registry.PromptUnavailable):
        registry.resolve_reference("talky/en-reply", version=999)
    assert cloud.calls[-1] == ("talky/en-reply", 999, None)


def test_local_mode_does_not_require_credentials_and_detects_changed_text(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setenv("LANGFUSE_PROMPT_VERSIONS", "{}")
    get_settings.cache_clear()
    try:
        reference = registry.resolve_reference("talky/en-reply")
        assert reference["source"] == "builtin"
        monkeypatch.setattr(registry, "builtin_text", lambda _name: "new local prompt")
        with pytest.raises(registry.PromptUnavailable):
            registry.read_reference(reference)
        with pytest.raises(registry.PromptUnavailable):
            registry.resolve_reference("talky/en-reply", version=1)
    finally:
        get_settings.cache_clear()


def test_publish_is_repeatable_without_creating_duplicate_versions(cloud):
    manage_prompts.publish("staging")
    manage_prompts.publish("staging")
    assert len(cloud.prompts) == len(registry.CATALOG)
    assert all("staging" in prompt.labels for prompt in cloud.prompts.values())


def test_user_upload_verifies_body_and_reuses_same_record(cloud):
    first = registry.publish_user_prompt("alice", "English tutor for Alice", record_id="record-1")
    assert registry.publish_user_prompt("alice", "English tutor for Alice", record_id="record-1") == first
    second = registry.publish_user_prompt("bob", "English tutor for Bob", record_id="record-2")
    assert first["name"] != second["name"]
    assert registry.read_reference(first) == "English tutor for Alice"


def test_generation_links_the_selected_version_and_records_tokens(cloud):
    reference = registry.resolve_reference("talky/en-reply")
    state = {"common_prompt_refs": {"talky/en-reply": reference}, "prompt_version_id": "db-prompt-1", "trace_id": "1" * 32}
    answer = AIMessage(content="Hello!", usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12})
    result = registry.invoke_with_prompt(SimpleNamespace(invoke=lambda _messages: answer), [HumanMessage(content="Hi")], state, "talky/en-reply")
    assert result is answer
    observed = cloud.observations[0]
    assert observed["start"]["prompt"].version == 1
    assert observed["start"]["metadata"]["db_prompt_id"] == "db-prompt-1"
    assert observed["usage_details"] == {"input": 10, "output": 2, "total": 12}
    assert observed["ended"] is True


def test_trace_failure_does_not_retry_or_lose_the_llm_result(cloud, monkeypatch):
    calls = []
    def unavailable(**_kwargs):
        raise ConnectionError("telemetry unavailable")
    monkeypatch.setattr(cloud, "start_observation", unavailable)
    answer = AIMessage(content="Hello!")
    assert registry.invoke_with_prompt(SimpleNamespace(invoke=lambda _messages: calls.append(1) or answer), [], {}, "talky/en-reply") is answer
    assert calls == [1]


def test_llm_error_is_recorded_and_propagated_once(cloud):
    calls = []
    def unavailable(_messages):
        calls.append(1)
        raise TimeoutError("LLM timeout")
    with pytest.raises(TimeoutError, match="LLM timeout"):
        registry.invoke_with_prompt(SimpleNamespace(invoke=unavailable), [], {}, "talky/en-reply")
    assert calls == [1]
    assert cloud.observations[0]["level"] == "ERROR"
    assert cloud.observations[0]["ended"] is True


def test_sdk_generation_export_works_without_langchain_callback():
    from langfuse import Langfuse
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    client = Langfuse(public_key="pk-test-prompt-link", secret_key="sk-test-prompt-link", tracer_provider=TracerProvider(), span_exporter=exporter)
    prompt = TextPromptClient(Prompt_Text(type="text", name="talky/en-reply", version=4, prompt="Reply in English", labels=[], config={}, tags=[]))
    try:
        generation = client.start_observation(name="test-generation", as_type="generation", prompt=prompt)
        generation.update(output="Hello!")
        generation.end()
        client.flush()
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].attributes["langfuse.observation.prompt.name"] == "talky/en-reply"
        assert spans[0].attributes["langfuse.observation.prompt.version"] == 4
    finally:
        client.shutdown()
