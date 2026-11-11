from contextlib import contextmanager
from types import SimpleNamespace
import json
import pytest
from app.agents.nodes import memory, memory_gate


@pytest.fixture
def router(monkeypatch):
    cfg = memory_gate.get_settings().model_copy(update={'MEMORY_GATE_ENABLED':True})
    monkeypatch.setattr(memory_gate,'get_settings',lambda:cfg)
    calls = []
    output = SimpleNamespace(content='0', response_metadata={'done_reason':'stop'})
    class Model:
        def invoke(self, messages):
            calls.append(messages)
            return output
    @contextmanager
    def slot(kind, **kwargs):
        assert kind == 'inference' and kwargs == {'max_wait_seconds':.25}
        yield
    monkeypatch.setattr(memory_gate, 'get_memory_gate_llm', lambda: Model())
    monkeypatch.setattr(memory_gate, 'resource_slot', slot)
    return output, calls


@pytest.mark.parametrize('content,expected', [('0',False), ('1',True), (' 0\n',False)])
def test_strict_binary_contract(router, content, expected):
    output, calls = router
    output.content = content
    assert memory_gate.recall_decision('Hello!', [])[0] is expected
    assert len(calls) == 1


@pytest.mark.parametrize('content,done', [('false','stop'), ('','stop'), ('0 because','stop'), ('"0"','stop'), ('0','length'), ('0',None)])
def test_invalid_or_truncated_output_keeps_search(router, content, done):
    router[0].content = content
    router[0].response_metadata = {'done_reason':done}
    assert memory_gate.recall_decision('Hello!', []) == (True,'fallback_invalid')


def test_timeout_and_insufficient_budget_keep_search(router, monkeypatch):
    from app.runtime import job_budget
    def fail():
        raise TimeoutError('busy')
    monkeypatch.setattr(memory_gate,'get_memory_gate_llm',fail)
    assert memory_gate.recall_decision('Hello!',[]) == (True,'fallback_error')
    with job_budget(2):
        assert memory_gate.recall_decision('Hello!',[]) == (True,'fallback_error')
    assert not router[1]


def test_recent_context_is_input_data(router):
    history = [{'role':'user','content':'old'}, {'role':'assistant','content':'Shall we practice?'}, {'role':'user','content':'Yes'}]
    memory_gate.recall_decision('Okay',history)
    data = json.loads(router[1][0][1].content)
    assert data == {'recent_chat':history[-2:],'current_utterance':'Okay'}


def test_skip_defers_indexing_without_reading_memory(router, monkeypatch):
    def forbidden(*args,**kwargs):
        pytest.fail('skip performed memory read or synchronous indexing')
    for name in ['_extract_topics','recall_related','active_conversation_ids']:
        monkeypatch.setattr(memory,name,forbidden)
    assert memory.memory_recall_node({'user_id':'u','user_text':'I moved to Busan.'}) == {
        'retrieved_memory':[], 'memory_topics':[], 'memory_topics_pending':True,
        'memory_recall_decision':'model_skip'}


def test_gate_uses_only_local_ollama_even_with_openai_provider(monkeypatch):
    from app.llm import factory
    import langchain_ollama
    cfg = SimpleNamespace(OLLAMA_BASE_URL='http://localhost:11434', MEMORY_GATE_MODEL='ax4-light:latest', MODEL_NAME='other',
                          LLM_CONTEXT_TOKENS=16384, MEMORY_GATE_TIMEOUT_SECONDS=3, use_openai=True)
    monkeypatch.setattr(factory,'get_settings',lambda:cfg)
    monkeypatch.setattr(langchain_ollama,'ChatOllama',lambda **kwargs:kwargs)
    factory.get_memory_gate_llm.cache_clear()
    try:
        model = factory.get_memory_gate_llm()
        assert model['model'] == 'ax4-light:latest'
        assert model['format'] == {'type':'integer','enum':[0,1]}
        assert model['num_predict'] == 4 and model['temperature'] == 0
        assert model['client_kwargs']['timeout'] == 3
    finally:
        factory.get_memory_gate_llm.cache_clear()


def test_disabled_gate_does_not_call_a_model_or_capacity_lock(monkeypatch):
    monkeypatch.setattr(memory_gate,'get_settings',lambda:SimpleNamespace(MEMORY_GATE_ENABLED=False))
    def forbidden(*args,**kwargs):
        pytest.fail('disabled gate made an external call')
    monkeypatch.setattr(memory_gate,'get_memory_gate_llm',forbidden)
    monkeypatch.setattr(memory_gate,'resource_slot',forbidden)
    assert memory_gate.recall_decision('Hello!',[]) == (True,'disabled')
