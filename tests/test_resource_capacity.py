import threading
import time
from concurrent.futures import ThreadPoolExecutor
from app.runtime import resource_slot


def test_cpu_stage_does_not_hold_inference_capacity(pg_db):
    entered, release = threading.Event(), threading.Event()
    def cpu_work():
        with resource_slot('audio'):
            entered.set()
            assert release.wait(3)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(cpu_work)
        try:
            assert entered.wait(1)
            start = time.monotonic()
            with resource_slot('inference'):
                assert time.monotonic()-start < .5
        finally:
            release.set()
        pending.result()


def test_inference_capacity_serializes_generation(pg_db):
    entered, release, second = threading.Event(), threading.Event(), threading.Event()
    def first():
        with resource_slot('inference'):
            entered.set()
            assert release.wait(3)
    def later():
        with resource_slot('inference'):
            second.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first)
        assert entered.wait(1)
        b = pool.submit(later)
        try:
            assert not second.wait(.2)
        finally:
            release.set()
        a.result(); b.result()
    assert second.is_set()


def test_factory_model_generation_enters_capacity_boundary(monkeypatch):
    from contextlib import contextmanager
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_ollama import ChatOllama
    from app.llm import capacity
    calls=[]
    @contextmanager
    def slot(kind):
        calls.append(kind)
        yield
    monkeypatch.setattr(capacity, 'resource_slot', slot)
    monkeypatch.setattr(ChatOllama, '_generate', lambda *_a, **_kw: ChatResult(generations=[ChatGeneration(message=AIMessage(content='OK'))]))
    model=capacity.SlotChatOllama(model='unused')
    assert model.bind(temperature=0).invoke('Hi').content == 'OK'
    assert calls == ['inference']
